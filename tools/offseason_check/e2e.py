"""`e2e`: one offseason through the league office on a copy of a real save and a fresh scratch league.

The flow is the auditor's verify_e2e / verify_final / verify_last run, packaged:

1. **The save.** The original is checked (`safety.check_save_path`), its sha256 taken (and compared with a
   `data/baselines/*.sha256` pin that names it), and it is copied into a temp work folder. Only the copies are ever
   written; the original's sha256 is re-checked after every write and at the end, and any change fails loudly.
2. **The league.** A new `mu_scratch_offseason_<random>_test` database from db/init_db.sql, with the seed's coach
   and coordinator ids offset as in the auditor's runs (the carousel's draws are keyed by id), then
   `db/seed_clean_db.py --season <season>`. The copy is read (pc_save_reader.mjs) and loaded
   (`pipeline.load_save --commit`, which runs coach_link: links seats, creates Mu's men over Madden's made-up
   coaches and files the starting staff's deals), then `pipeline.league_office setup` writes the league copy.
3. **The carousel.** A scripted Black Monday (five head coaches and nine coordinators fired, as the auditor's
   fixture did) and the real `coaching_carousel.hiring_cycle(season)`, which files its hires' deals. With the career
   fixture on (the default), Reid and J. Harbaugh get their pre-season career records first, so a retread can win.
4. **The writes.** `pipeline.league_office` at OFFSEASON_START, RESIGN, FREE_AGENCY wave 1 and one catch-up stage
   (COMBINE), each on the league copy with the real save writer, after reading the copy fresh.

After every write: the write itself, the original's sha256, pc_save_reader's read-back, the staff-contract invariant
(projected through annual(season) and annual(season + 1) in a rolled-back transaction), one live row per person,
every seated coach's Contract* against his row, user wiring (verifySetup, all four links, 32 teams, no empty seat)
and an empty re-plan (`coach_save_sync.plan_staff_edits`). After the load: the link's counts and the F1 check
(`start_years_from_save`). The scratch DB is dropped and the copies deleted unless --keep.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from tools.offseason_check import checks, invariant as inv, report as rp, safety, scratch
from tools.offseason_check.report import Check, FAIL, INFO, passed

ROOT = scratch.ROOT
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
NODE_MODULES = os.path.join(ROOT, "pipeline", "save_reader", "node_modules", "madden-franchise")
PIN_DIRS = [os.path.join(ROOT, "data", "baselines"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(ROOT))), "data", "baselines")]

# The auditor's Black Monday fixture (verify_e2e carousel_e2e.py): with the seed id offset and the career records
# below, hiring_cycle(2026) gives NE an OUTSIDE hire, HOU a RETREAD (J. Harbaugh) and KC, NYG, SF POACHED ones.
FIRED_HEAD_COACH_TEAMS = ("KC", "NYG", "NE", "HOU", "SF")
FIRED_COORDINATORS = ("Daronte Jones", "Gus Bradley", "Adam Stenavich", "Ejiro Evero", "Andrew Janocko",
                      "Vic Fangio", "Mike Rutenberg", "Dennis Allen", "Tommy Rees")
CAREER_FIXTURE = (("Andy Reid", 273, 146, 28, 3), ("John Harbaugh", 180, 113, 13, 1))
WRITES = (("OFFSEASON_START", None), ("RESIGN", None), ("FREE_AGENCY", 1), ("COMBINE", None))


def last_json(text):
    """The last top-level JSON object in a command's stdout (league_office prints its report last), or None."""
    text = text or ""
    pos = len(text)
    while True:
        pos = text.rfind("\n{", 0, pos)
        start = pos + 1 if pos >= 0 else (0 if text.startswith("{") else -1)
        if start < 0:
            return None
        try:
            return json.loads(text[start:])
        except ValueError:
            if pos < 0:
                return None


class E2E:
    def __init__(self, save, season=2026, keep=False, career_fixture=True, expectations=None, workdir=None,
                 log=print):
        self.season, self.keep, self.career_fixture, self.log = int(season), keep, career_fixture, log
        self.expectations = expectations or []
        self.checks, self.meta = [], {"season": self.season}
        self.guard = safety.ShaGuard(save)
        self.meta.update(save=self.guard.path, save_sha256=self.guard.before)
        base = safety.check_writable_path(workdir or tempfile.gettempdir())
        self.work = safety.check_writable_path(tempfile.mkdtemp(prefix="offseason_check_", dir=base))
        self.src = os.path.join(self.work, "SOURCE")
        self.league = os.path.join(self.work, "LEAGUE")
        self.name = self.dsn = None

    # -------------------------------------------------------------------------------------------- plumbing
    def add(self, name, stage, ok_or_status, detail="", **data):
        status = ok_or_status if isinstance(ok_or_status, str) else passed(ok_or_status)
        c = Check(name, stage, status, detail, data)
        self.checks.append(c)
        self.log(f"  {status:<4} {stage:<16} {name}: {detail[:160]}")
        return c

    def child(self, args, log_name, timeout=3600):
        env = scratch.child_env(self.dsn)
        t0 = time.time()
        p = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        with open(os.path.join(self.work, log_name), "w") as fh:
            fh.write(f"$ {' '.join(args)}\n# exit {p.returncode} after {time.time() - t0:.0f}s\n")
            fh.write("## stdout\n" + p.stdout + "\n## stderr\n" + p.stderr)
        return p

    def read(self, save, out_name):
        out = os.path.join(self.work, out_name)
        p = subprocess.run(["node", READER, save, "--out", out], cwd=ROOT, capture_output=True, text=True,
                           timeout=1800)
        if p.returncode != 0:
            raise RuntimeError(f"pc_save_reader.mjs failed on {save}: {p.stderr.strip()[-800:]}")
        with open(out) as fh:
            return json.load(fh)

    def cursor_do(self, fn, rollback=True):
        with safety.connect_scratch(self.dsn) as conn:
            cur = conn.cursor()
            try:
                out = fn(cur)
                if rollback:
                    conn.rollback()
                else:
                    conn.commit()
                return out
            except Exception:
                conn.rollback()
                raise

    def check_original(self, stage):
        try:
            sha = self.guard.verify()
            self.add("original_unchanged", stage, True, f"sha256 {sha[:12]}... unchanged")
        except safety.SafetyError as e:
            self.add("original_unchanged", stage, False, str(e))
            raise

    # -------------------------------------------------------------------------------------------- steps
    def preflight(self):
        if not os.path.isdir(NODE_MODULES):
            raise RuntimeError(f"{NODE_MODULES} is missing: run `npm ci` in pipeline/save_reader (or, in a worktree, "
                               "symlink the main checkout's pipeline/save_reader/node_modules)")
        pin = safety.pinned_sha(self.guard.path, PIN_DIRS)
        if pin is not None:
            self.add("original_matches_pin", "preflight", pin == self.guard.before,
                     f"pin {pin[:12]}..., file {self.guard.before[:12]}...")
            if pin != self.guard.before:
                raise safety.SafetyError("the save no longer matches its data/baselines pin; refusing to use it")
        else:
            self.add("original_matches_pin", "preflight", INFO, "no data/baselines pin names this save")
        shutil.copyfile(self.guard.path, self.src)
        copy_sha = safety.sha256_file(self.src)
        self.add("copy_made", "preflight", copy_sha == self.guard.before, f"{self.src} sha256 {copy_sha[:12]}...")

    def build_league(self):
        self.name = scratch.new_name("offseason")
        self.dsn = scratch.create(self.name)
        self.meta["scratch_db"] = self.name
        self.log(f"scratch DB {self.name} created")
        scratch.init_schema(self.dsn, scratch.SEED_ID_OFFSETS)
        scratch.seed(self.dsn, self.season)
        self.snap0 = self.read(self.src, "snap0.json")
        p = self.child([sys.executable, "-m", "pipeline.load_save", os.path.join(self.work, "snap0.json"),
                        "--season", str(self.season), "--commit"], "load_save.log")
        if p.returncode != 0:
            raise RuntimeError(f"load_save failed: {p.stdout[-1500:]} {p.stderr[-1500:]}")
        link = json.loads(p.stdout[p.stdout.index("{"):]).get("coach_link") or {}
        self.meta["coach_link"] = {k: link.get(k) for k in ("linked", "created", "linked_free_agents",
                                                             "contracts_filed")}
        self.meta["coach_link"]["unmatched"] = len(link.get("unmatched") or [])
        self.check_league_start(link)
        p = self.child([sys.executable, "-m", "pipeline.league_office", "setup", "--save", self.src, "--out",
                        self.league, "--dsn", self.dsn], "setup.log")
        if p.returncode != 0:
            raise RuntimeError(f"league_office setup failed: {p.stderr[-1500:]}")
        self.add("league_setup", "setup", os.path.isfile(self.league), "league_office setup wrote the league copy")

    def check_league_start(self, link):
        from engines import staff_contracts_run as scr
        season = self.season

        def gather(cur):
            staff = scr.current_staff(cur, season)
            rows = checks.db_live_rows(cur)
            profiles = checks.db_profile_of(cur)
            names = {(s["person_kind"], s["person_id"]): s["name"] for s in staff}
            return staff, rows, profiles, names, inv.team_names(cur), checks.db_duplicates(cur)
        staff, rows, profiles, names, teams, (dups, exact) = self.cursor_do(gather)
        c = self.meta["coach_link"]
        self.add("link_counts", "load", INFO, f"linked {c['linked']}, created {c['created']}, free agents linked "
                 f"{c['linked_free_agents']}, contracts filed {c['contracts_filed']}, unmatched {c['unmatched']}",
                 **c)
        by_key = {}
        for r in rows:
            by_key.setdefault((r["team_id"], r["person_kind"], r["person_id"]), []).append(r)
        missing = [s for s in staff if (s["team_id"], s["person_kind"], s["person_id"]) not in by_key]
        self.add("league_start_rows", "load", not missing and not dups and not exact,
                 f"{len(staff)} current staff, {len(rows)} live rows, {len(missing)} without a row, "
                 f"{len(dups)} with two live rows, {len(exact)} exact duplicates",
                 missing=[f"{s['person_kind']} {s['name']}" for s in missing[:20]])
        # F1: the starting coaches' years come from Madden's save (JR via M-0225).
        coaches = {x.get("pgid"): x for x in self.snap0.get("coaches") or []}
        by_person = {(k, pid): pg for pg, (k, pid, _src) in profiles.items()}
        kind_of = {"HC": "COACH"}

        def save_contract(team_id, kind, pid):
            if kind == "GM":
                return None
            pg = by_person.get((kind_of.get(kind, "COORD"), pid))
            co = coaches.get(pg)
            if co is None:
                return None
            return co.get("contract_length"), co.get("contract_years_remaining")

        def live_row(team_id, kind, pid):
            got = [r for r in by_key.get((team_id, kind, pid), []) if int(r["signed_season"]) <= season]
            return got[0] if len(got) == 1 else None
        n, problems = checks.start_years_problems(staff, live_row, save_contract, scr._years, season)
        self.add("start_years_from_save", "load", not problems,
                 f"{n} league-start rows, {len(problems)} whose years differ from Madden's save (F1, M-0225)"
                 + (f"; e.g. {problems[0]}" if problems else ""), problems=problems)
        if self.expectations:
            ep = checks.expectation_problems(rows, names, teams, self.expectations)
            self.add("expectations", "load", not ep, f"{len(self.expectations)} expected rows, {len(ep)} problems"
                     + (f"; e.g. {ep[0]}" if ep else ""), problems=ep)

    def carousel(self):
        from engines import coaching_carousel as cc
        season = self.season

        def go(cur):
            if self.career_fixture:
                for name, w, l, pw, sb in CAREER_FIXTURE:
                    cur.execute("""INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses,
                                   career_ties, winning_seasons, playoff_wins, playoff_losses, super_bowl_wins,
                                   super_bowl_losses)
                                   SELECT c.id, ct.team_id, %s, %s, %s, 0, 15, %s, 10, %s, 1 FROM coaches c
                                   JOIN coach_tenures ct ON ct.coach_id = c.id WHERE c.coach_name = %s""",
                                (season - 1, w, l, pw, sb, name))
            cur.execute("""SELECT t.id, t.abbreviation, ct.coach_id FROM teams t JOIN coach_tenures ct
                           ON ct.team_id = t.id AND ct.end_season IS NULL WHERE t.abbreviation = ANY(%s)""",
                        (list(FIRED_HEAD_COACH_TEAMS),))
            fired = cur.fetchall()
            for tid, _ab, cid in fired:
                cur.execute("INSERT INTO coach_hot_seat (season, team_id, coach_id, score, fired, inputs) "
                            "VALUES (%s,%s,%s,90,true,'{}')", (season, tid, cid))
                cur.execute("UPDATE coach_tenures SET end_season=%s WHERE coach_id=%s AND team_id=%s "
                            "AND end_season IS NULL", (season, cid, tid))
            for name in FIRED_COORDINATORS:
                cur.execute("""SELECT t.coordinator_id, t.team_id, t.role FROM coordinator_tenures t
                               JOIN coordinators c ON c.id = t.coordinator_id
                               WHERE c.coordinator_name = %s AND t.end_season IS NULL""", (name,))
                got = cur.fetchall()
                if len(got) != 1:
                    raise RuntimeError(f"fixture coordinator {name!r}: {len(got)} open tenures in the seed")
                co, tid, role = got[0]
                cur.execute("INSERT INTO coordinator_hot_seat (season, team_id, role, coordinator_id, score, fired, "
                            "phase, inputs) VALUES (%s,%s,%s,%s,90,true,'BLACK_MONDAY','{}')",
                            (season, tid, role, co))
                cur.execute("UPDATE coordinator_tenures SET end_season=%s WHERE coordinator_id=%s "
                            "AND end_season IS NULL", (season, co))
            rep = cc.hiring_cycle(cur, season, log=lambda *a, **k: None)
            return len(fired), rep
        n_fired, rep = self.cursor_do(go, rollback=False)
        hcs = [(h.get("team"), h.get("source"), h.get("coach")) for h in rep.get("head_coaches") or []]
        self.meta["carousel"] = {"head_coaches": hcs, "coordinators": len(rep.get("coordinators") or [])}
        sources = {s for _t, s, _c in hcs}
        self.add("carousel_hires", "carousel", INFO,
                 f"{n_fired} HCs fired; hires " + ", ".join(f"{t} {s} {c}" for t, s, c in hcs)
                 + f"; {len(rep.get('coordinators') or [])} coordinators"
                 + ("" if {"OUTSIDE", "POACHED", "RETREAD"} <= sources
                    else " (not the full OUTSIDE/POACHED/RETREAD mix)"))
        self.check_hire_rows("carousel")

    def check_hire_rows(self, stage):
        """Every hire for season+1 has exactly one live row, signed season+1 (filed at the hire, M-0200)."""
        from engines import staff_contracts_run as scr
        nxt = self.season + 1

        def gather(cur):
            cur_keys = {(s["team_id"], s["person_kind"], s["person_id"]) for s in scr.current_staff(cur, self.season)}
            hires = [s for s in scr.current_staff(cur, nxt) if (s["team_id"], s["person_kind"], s["person_id"])
                     not in cur_keys and s["person_kind"] != "GM"]
            rows = inv.snapshot_rows(cur)
            return hires, rows
        hires, rows = self.cursor_do(gather)
        bad = []
        for s in hires:
            mine = [r for r in rows if (r["team_id"], r["person_kind"], r["person_id"])
                    == (s["team_id"], s["person_kind"], s["person_id"])]
            live = [r for r in mine if int(r["remaining_years"]) > 0]
            if len(live) != 1 or int(live[0]["signed_season"]) != nxt or len(mine) != 1:
                shape = [(r["signed_season"], r["remaining_years"]) for r in mine]
                bad.append(f"{s['person_kind']} {s['name']}: rows (signed, remaining) {shape}")
        self.add("hire_rows", stage, not bad, f"{len(hires)} hires for {nxt}, {len(bad)} without exactly one live "
                 f"row signed {nxt}" + (f"; e.g. {bad[0]}" if bad else ""), problems=bad)

    def write(self, stage, wave, n):
        from engines import coach_save_sync as cs
        label = stage if wave is None else f"{stage}:{wave}"
        snap_before = self.read(self.league, f"snap_before_{n}.json")
        if n == 1:
            plan = self.cursor_do(lambda cur: cs.plan_staff_edits(cur, snap_before, self.season + 1))
            counts = checks.staff_action_counts(plan["staff"])
            c = self.meta["coach_link"]
            self.add("seat_linking", label, not plan["skipped"],
                     f"linked {c['linked']} / created {c['created']} / released {counts['release']} / skipped "
                     f"{len(plan['skipped'])} (plan: {counts['assign']} assign, {counts['create']} create, "
                     f"{counts['with_contract']} with a contract)", skipped=plan["skipped"], **counts)
        args = [sys.executable, "-m", "pipeline.league_office", "--snapshot",
                os.path.join(self.work, f"snap_before_{n}.json"), "--save", self.league, "--stage", stage,
                "--dsn", self.dsn, "--confirm-franchise-closed"]
        if wave is not None:
            args += ["--wave", str(wave)]
        p = self.child(args, f"league_office_{n}_{stage.lower()}.log")

        def last_run(cur):
            cur.execute("SELECT id, status, stage, error, edits, summary FROM league_office_runs ORDER BY id DESC "
                        "LIMIT 1")
            return cur.fetchone()
        rid, status, _st, error, edits, summary = self.cursor_do(last_run)
        staff = (edits or {}).get("staff") or []
        counts = checks.staff_action_counts(staff)
        self.add("write", label, status == "WRITTEN" and p.returncode == 0,
                 f"run {rid} {status}; staff {counts['release']} release / {counts['assign']} assign / "
                 f"{counts['create']} create, {counts['with_contract']} with a contract; "
                 f"{len((edits or {}).get('players') or [])} player edits"
                 + (f"; error: {error[:300]}" if error else ""),
                 run_id=rid, **counts)
        self.check_original(label)
        try:
            snap = self.read(self.league, f"snap_after_{n}.json")
            ok = len(snap.get("staff") or []) == 32 and bool(snap.get("coaches"))
            self.add("read_back", label, ok, f"pc_save_reader read the copy: {len(snap.get('players') or [])} players, "
                     f"{len(snap.get('coaches') or [])} coaches, {len(snap.get('staff') or [])} staffed teams")
        except Exception as e:      # noqa: BLE001
            self.add("read_back", label, False, f"{type(e).__name__}: {e}")
            return
        target = int(((summary or {}).get("target_season")) or self.season + 1)
        rep = self.check_ledger(label)
        if n == len(WRITES):
            proj = checks.projection_summary(rep, self.season)
            self.meta["projected_next_season"] = proj
            fired = "; ".join(f"{t} {k} {w} ${int(b):,} over {y}" for t, k, w, b, y in proj["buyouts"]
                              if k == "HC")
            self.add("projected_resigns_buyouts", label, INFO,
                     f"annual({self.season + 1}) projected: {len(proj['resigned'])} re-signed, "
                     f"{len(proj['buyouts'])} buyouts from {self.season + 1}; HC buyouts: {fired or 'none'}", **proj)
        self.check_seats(label, snap)
        self.check_wiring(label)
        replan = self.cursor_do(lambda cur: cs.plan_staff_edits(cur, snap, target))
        self.add("replan_empty", label, not replan["staff"],
                 f"plan_staff_edits(..., {target}) on the written copy: {len(replan['staff'])} actions, "
                 f"{len(replan['skipped'])} skipped", actions=replan["staff"][:20])
        if stage == "FREE_AGENCY":
            steps = (last_json(p.stdout) or {}).get("annual_steps") or {}
            sc = steps.get("staff_contracts") or {}
            self.meta["fa_staff_contracts"] = sc
            self.add("annual_after_fa", label, INFO if sc.get("status") == "built" else FAIL,
                     f"staff_contracts.annual({self.season}): {sc.get('status')}, filed {sc.get('filed')}, rolled "
                     f"{sc.get('rolled')}, fired {sc.get('fired')}, payroll ${int(sc.get('payroll') or 0):,}", **sc)
            self.check_hire_rows(label)

    def check_ledger(self, label):
        def go(cur):
            dups, exact = checks.db_duplicates(cur)
            rep = inv.project(cur, [self.season, self.season + 1])
            return dups, exact, rep
        dups, exact, rep = self.cursor_do(go)          # rolled back: the projection never sticks
        self.add("one_live_row", label, not dups and not exact,
                 f"{len(dups)} people with two live rows, {len(exact)} exact duplicate rows",
                 live=[list(x) for x in dups], exact=[list(x) for x in exact])
        bits = []
        if rep["issues"]:
            bits.append(f"{len(rep['issues'])} deal issues, e.g. {rep['issues'][0]}")
        if rep["held_unpaid"]:
            u = rep["held_unpaid"][0]
            bits.append(f"{len(rep['held_unpaid'])} held seasons not paid once, e.g. {u['year']} {u['team']} "
                        f"{u['kind']} {u['who']} ({u['payments']})")
        if rep["team_checks"]:
            bits.append(f"{len(rep['team_checks'])} payroll mismatches")
        self.add("invariant", label, rep["ok"], "; ".join(bits) or
                 f"{len(rep['deals'])} deals, every season {self.season}-{self.season + 1} paid exactly once "
                 "(projected through annual, rolled back)", issues=rep["issues"][:30],
                 held_unpaid=rep["held_unpaid"][:30])
        return rep

    def check_seats(self, label, snap):
        from engines import coach_save_sync as cs

        def go(cur):
            profiles = checks.db_profile_of(cur)
            team_of, _abbr = cs._team_map(cur, snap)
            return checks.seat_contract_problems(snap, profiles, team_of,
                                                 lambda t, k, p: cs._contract(cur, t, k, p, True))
        seats, by_source, problems = self.cursor_do(go)
        self.add("seat_contracts", label, not problems and seats == 96,
                 f"{seats} seats ({', '.join(f'{k} {v}' for k, v in sorted(by_source.items()))}), {len(problems)} "
                 "with a zero or mismatched Contract*" + (f"; e.g. {problems[0]}" if problems else ""),
                 problems=problems[:40])

    def check_wiring(self, label):
        try:
            w = checks.save_wiring(self.league)
        except Exception as e:      # noqa: BLE001
            self.add("user_wiring", label, False, f"{type(e).__name__}: {e}")
            return
        ok = not w["problems"] and not w["empty_seats"] and w["users"] == 32
        self.add("user_wiring", label, ok, f"verifySetup: {len(w['problems'])} problems, {w['users']} users, "
                 f"{len(w['empty_seats'])} empty seats" + (f"; e.g. {w['problems'][0]}" if w["problems"] else ""),
                 **w)

    # -------------------------------------------------------------------------------------------- run
    def run(self):
        try:
            self.preflight()
            self.build_league()
            self.carousel()
            for n, (stage, wave) in enumerate(WRITES, 1):
                self.write(stage, wave, n)
        except Exception as e:      # noqa: BLE001 - every failure is a FAIL row, then cleanup
            self.add("run", "error", FAIL, f"{type(e).__name__}: {e}")
        finally:
            try:
                self.check_original("end")
            except safety.SafetyError:
                pass
            self.cleanup()
        return self.checks

    def cleanup(self):
        if self.keep:
            self.log(f"kept (--keep): work folder {self.work}" + (f", scratch DB {self.name}" if self.name else ""))
            return
        if self.name:
            scratch.drop(self.name)
            self.log(f"scratch DB {self.name} dropped")
        shutil.rmtree(self.work, ignore_errors=True)
        self.log(f"work folder {self.work} deleted")


def run(save, season=2026, keep=False, json_out=None, career_fixture=True, known_open=(), expect=None,
        workdir=None, log=print):
    expectations = []
    if expect:
        with open(expect) as fh:
            expectations = json.load(fh)
    if json_out:
        safety.check_writable_path(json_out)
    e = E2E(save, season, keep, career_fixture, expectations, workdir, log)
    log(f"offseason_check e2e: {e.guard.path} (sha256 {e.guard.before[:12]}...) -> copies in {e.work}")
    found = rp.apply_known_open(e.run(), known_open)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    e.meta.update(git_head=head, known_open=list(known_open))
    if json_out:
        with open(json_out, "w") as fh:
            fh.write(rp.to_json(found, command="e2e", **e.meta))
    return found, e.meta
