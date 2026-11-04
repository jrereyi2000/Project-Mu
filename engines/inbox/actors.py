"""
Which personas JR controls (FRONT_OFFICE_INBOX_PLAN.md §2.1).

A thread is always (a persona JR controls) <-> (a counterpart agent). JR controls no one by default (D8): a persona
is controlled only once JR takes control of it, which stores an active `controlled_personas` row. Only teams in the
active franchise registry's `user_teams` count (D6); rows of any other team are ignored. A head coach who is also
his team's GM (`gm_profiles.coach_id`) is one candidate ("HC & GM"): taking control of him stores both rows.

  user_teams() -> [ABBR]                         db.franchises.active_entry()['user_teams'], upper case
  controlled(cur, abbr) -> [Actor]               active rows of a registry user team, GM first; else []
  actor_for(cur, abbr, role) -> Actor | None
  candidates(cur, abbr) -> [{role, persona_id, name, also}]
  take_control(cur, abbr, role, persona_id) -> Actor     NotUserTeam (403) / Conflict (409) / NotFound (404)
  release_control(cur, abbr, role, persona_id)           active = false; its threads stay readable
  is_active(cur, team_id, role, persona_id) -> bool

Players: a PLAYER row is accepted (the persona must be on the team in his newest player_seasons row), but no kind
serves a PLAYER actor yet (controlled-player mode is designed later) and candidates() doesn't list players.
Nothing here commits.
"""
from dataclasses import dataclass

from engines.inbox import Conflict, InboxError, NotFound, NotUserTeam

ROLES = ("GM", "COACH", "PLAYER")
ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}


@dataclass(frozen=True)
class Actor:
    team_id: int
    abbr: str
    role: str            # 'GM' | 'COACH' | 'PLAYER'
    persona_id: str      # gm_profiles.id | coaches.id | players.id, as text
    name: str

    def to_dict(self):
        """The API's Actor shape (§2.12)."""
        return {"role": self.role, "id": self.persona_id, "name": self.name}


def user_teams():
    """The active franchise's user teams, upper case; [] when none are registered or the registry can't be read."""
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
    except Exception:
        return []
    return [str(t).upper() for t in ((entry or {}).get("user_teams") or [])]


def installed(cur):
    cur.execute("SELECT to_regclass('controlled_personas') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def team_row(cur, abbr):
    """(team_id, ABBR) or None."""
    cur.execute("SELECT id, abbreviation FROM teams WHERE abbreviation = %s", (str(abbr).upper(),))
    row = cur.fetchone()
    return (row[0], str(row[1]).upper()) if row else None


def _int_id(persona_id):
    try:
        return int(str(persona_id).strip())
    except (TypeError, ValueError):
        return None


def persona_name(cur, role, persona_id):
    """The persona's display name (falls back to the id)."""
    pid = _int_id(persona_id)
    sql = {"GM": "SELECT gm_name FROM gm_profiles WHERE id = %s",
           "COACH": "SELECT coach_name FROM coaches WHERE id = %s",
           "PLAYER": "SELECT player_name FROM players WHERE id = %s"}.get(role)
    if sql is None or pid is None:
        return str(persona_id)
    cur.execute(sql, (pid,))
    row = cur.fetchone()
    return row[0] if row and row[0] else str(persona_id)


def current_gm(cur, team_id):
    """{id, name, coach_id} of the team's open GM tenure, or None."""
    cur.execute("SELECT id, gm_name, coach_id FROM gm_profiles WHERE team_id = %s AND end_season IS NULL "
                "ORDER BY start_season DESC, id DESC LIMIT 1", (team_id,))
    row = cur.fetchone()
    return {"id": row[0], "name": row[1], "coach_id": row[2]} if row else None


def head_coach(cur, team_id):
    """{id, name} of the team's open head-coach tenure, or None."""
    cur.execute("SELECT coach_id FROM coach_tenures WHERE team_id = %s AND end_season IS NULL "
                "ORDER BY start_season DESC, id DESC LIMIT 1", (team_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "name": persona_name(cur, "COACH", row[0])}


def _player_on_team(cur, team_id, player_id):
    cur.execute("SELECT team_id FROM player_seasons WHERE player_id = %s ORDER BY season DESC LIMIT 1", (player_id,))
    row = cur.fetchone()
    return bool(row) and row[0] == team_id


def _pair(cur, team_id, role, pid):
    """The other half of a coach-GM persona: ('COACH', coach_id) for a GM whose coach_id is the head coach,
    ('GM', gm_id) for a head coach who is also the GM; else None."""
    gm, hc = current_gm(cur, team_id), head_coach(cur, team_id)
    if not gm or not hc or gm["coach_id"] is None or gm["coach_id"] != hc["id"]:
        return None
    if role == "GM" and gm["id"] == pid:
        return "COACH", hc["id"]
    if role == "COACH" and hc["id"] == pid:
        return "GM", gm["id"]
    return None


def _require_team(cur, abbr):
    up = str(abbr or "").upper()
    if up not in user_teams():
        raise NotUserTeam(f"{up} isn't one of your teams in the franchise registry")
    team = team_row(cur, up)
    if team is None:
        raise NotFound(f"no team {up}")
    return team


def is_active(cur, team_id, role, persona_id):
    cur.execute("SELECT active FROM controlled_personas WHERE team_id = %s AND role = %s AND persona_id = %s",
                (team_id, role, str(persona_id)))
    row = cur.fetchone()
    return bool(row and row[0])


def controlled(cur, abbr):
    """Active controlled personas of a registry user team (GM, then COACH, then PLAYER); [] otherwise (D8)."""
    up = str(abbr or "").upper()
    if up not in user_teams() or not installed(cur):
        return []
    team = team_row(cur, up)
    if team is None:
        return []
    cur.execute("SELECT role, persona_id, taken_at FROM controlled_personas WHERE team_id = %s AND active "
                "ORDER BY taken_at, role", (team[0],))
    rows = sorted(cur.fetchall(), key=lambda r: (ROLE_ORDER.get(r[0], 9), str(r[2]), str(r[1])))
    return [Actor(team[0], team[1], r[0], str(r[1]), persona_name(cur, r[0], r[1])) for r in rows]


def actor_for(cur, abbr, role):
    role = str(role or "").upper()
    return next((a for a in controlled(cur, abbr) if a.role == role), None)


def candidates(cur, abbr):
    """Who JR could take control of: the open GM tenure and the head coach ([] outside the registry). A coach-GM
    is one candidate: {"role": "GM", ..., "also": {"role": "COACH", "persona_id"}}."""
    up = str(abbr or "").upper()
    if up not in user_teams():
        return []
    team = team_row(cur, up)
    if team is None:
        return []
    gm, hc = current_gm(cur, team[0]), head_coach(cur, team[0])
    out = []
    if gm and hc and gm["coach_id"] is not None and gm["coach_id"] == hc["id"]:
        return [{"role": "GM", "persona_id": str(gm["id"]), "name": gm["name"],
                 "also": {"role": "COACH", "persona_id": str(hc["id"])}}]
    if gm:
        out.append({"role": "GM", "persona_id": str(gm["id"]), "name": gm["name"], "also": None})
    if hc:
        out.append({"role": "COACH", "persona_id": str(hc["id"]), "name": hc["name"], "also": None})
    return out


def _on_team(cur, team_id, role, pid):
    if role == "GM":
        gm = current_gm(cur, team_id)
        return bool(gm) and gm["id"] == pid
    if role == "COACH":
        hc = head_coach(cur, team_id)
        return bool(hc) and hc["id"] == pid
    return _player_on_team(cur, team_id, pid)


def _insert(cur, team_id, role, persona_id):
    cur.execute("INSERT INTO controlled_personas (team_id, role, persona_id) VALUES (%s, %s, %s) "
                "ON CONFLICT (team_id, role, persona_id) DO UPDATE SET active = true, released_at = NULL, "
                "taken_at = now()", (team_id, role, str(persona_id)))


def _check_role(role):
    role = str(role or "").upper()
    if role not in ROLES:
        raise InboxError(400, f"role must be one of {', '.join(ROLES)}")
    return role


def take_control(cur, abbr, role, persona_id):
    """Store an active controlled_personas row (and the pair's for a coach-GM). 403 outside the registry's
    user_teams, 409 when the persona isn't on that team."""
    role = _check_role(role)
    team_id, up = _require_team(cur, abbr)
    pid = _int_id(persona_id)
    if pid is None or not _on_team(cur, team_id, role, pid):
        raise Conflict(f"{role.lower()} {persona_id} isn't on {up}")
    _insert(cur, team_id, role, pid)
    pair = _pair(cur, team_id, role, pid) if role in ("GM", "COACH") else None
    if pair:
        _insert(cur, team_id, pair[0], pair[1])
    return Actor(team_id, up, role, str(pid), persona_name(cur, role, pid))


def release_control(cur, abbr, role, persona_id):
    """active = false (and the coach-GM pair's row). Its open threads stay readable but can't be answered."""
    role = _check_role(role)
    team_id, _ = _require_team(cur, abbr)
    pid = _int_id(persona_id)
    targets = [(role, str(persona_id))]
    if pid is not None and role in ("GM", "COACH"):
        pair = _pair(cur, team_id, role, pid)
        if pair:
            targets.append((pair[0], str(pair[1])))
    for r, p in targets:
        cur.execute("UPDATE controlled_personas SET active = false, released_at = now() "
                    "WHERE team_id = %s AND role = %s AND persona_id = %s AND active", (team_id, r, p))
