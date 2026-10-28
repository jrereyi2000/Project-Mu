"""Surname rename tool and Madden commentary ID lookup (pipeline/commentary_names.py).

Provides:
1. Normalization: drop suffixes (Jr, Sr, II, III, IV, V), strip accents and punctuation,
   match with/without hyphens, casefolding.
2. Commentary ID lookup: commentary_id(full_name, lookup=None) -> int | None.
3. Ranking / candidate suggestions: Double Metaphone phonetic similarity + Levenshtein distance,
   preferring same first letter and avoiding surnames already on the same team.
4. CLI subcommands:
   - report: computes coverage against active players (latest player_seasons, retired_season IS NULL,
     skipping DEF/team defense) or draft prospects, and writes review CSV with top 5 candidates.
   - apply <reviewed.csv> [--commit]: writes chosen surname to players.full_name and player_name,
     recording old names in player_name_aliases. Dry-run by default.
"""

from collections import defaultdict
import csv
import json
import os
import re
import sys
import unicodedata

try:
    import metaphone
except ImportError:
    metaphone = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COMMENTARY_PATH = os.path.join(ROOT, "data", "commentary_m27.json")
DEFAULT_DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

SUFFIX_PATTERN = re.compile(r",?\s+(Jr\.?|Sr\.?|II|III|IV|V)$", flags=re.IGNORECASE)

_COMMENTARY_RAW: dict[str, int] | None = None
_NORMALIZED_LOOKUP: dict[str, int] | None = None
_RECORDED_SURNAMES: list[str] | None = None
_RECORDED_METADATA: list[tuple[str, str, str, str, str]] | None = None  # (cand, c_norm, c_pri, c_sec, c_first)


# --------------------------------------------------------------------- loading ----
def load_commentary_lookup(path: str | None = None) -> dict[str, int]:
    """Load Madden 27 commentary lookup json mapping surname -> commentary ID.

    Raises FileNotFoundError with instructions if the file is missing.
    """
    global _COMMENTARY_RAW, _NORMALIZED_LOOKUP, _RECORDED_SURNAMES, _RECORDED_METADATA
    file_path = path or DEFAULT_COMMENTARY_PATH
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Commentary lookup file not found at '{file_path}'. "
            "Please fetch it using:\n"
            "curl -sSfL https://raw.githubusercontent.com/Sinthros/madden-franchise-utils/master/Utils/JsonLookups/27/playerLookups/commentary.json -o data/commentary_m27.json"
        )

    if _COMMENTARY_RAW is None or path is not None:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        lookup: dict[str, int] = {}
        for surname, cid in raw_data.items():
            cid_int = int(cid)
            lookup[surname.lower()] = cid_int

            # Without hyphens / punctuation
            clean_s = re.sub(r"[^a-z0-9]", "", surname.lower())
            if clean_s and clean_s not in lookup:
                lookup[clean_s] = cid_int

            # Without suffix if raw key contained one (e.g. 'Bolden Jr.')
            no_suf = drop_suffixes(surname).lower()
            if no_suf and no_suf not in lookup:
                lookup[no_suf] = cid_int

        rec_surnames = sorted(list(raw_data.keys()))
        rec_meta = []
        for s in rec_surnames:
            p, sec = _get_double_metaphone(s)
            rec_meta.append((s, normalize(s), p, sec, s[0].lower() if s else ""))

        if path is None:
            _COMMENTARY_RAW = raw_data
            _NORMALIZED_LOOKUP = lookup
            _RECORDED_SURNAMES = rec_surnames
            _RECORDED_METADATA = rec_meta
            return _NORMALIZED_LOOKUP
        return lookup

    return _NORMALIZED_LOOKUP


def get_recorded_surnames(path: str | None = None) -> list[str]:
    """Return list of original-cased recorded surnames from commentary json."""
    global _RECORDED_SURNAMES
    if _RECORDED_SURNAMES is None or path is not None:
        load_commentary_lookup(path)
    return _RECORDED_SURNAMES or []


# --------------------------------------------------------------- normalization ----
def strip_accents(text: str) -> str:
    """Strip accents/diacritics from text."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def drop_suffixes(text: str) -> str:
    """Drop common name suffixes (Jr, Sr, II, III, IV, V)."""
    if not text:
        return ""
    return SUFFIX_PATTERN.sub("", text).strip()


def normalize(surname: str) -> str:
    """Drop suffixes, casefold, strip accents and punctuation."""
    if not surname:
        return ""
    s = drop_suffixes(surname)
    s = strip_accents(s)
    s = s.casefold().strip()
    # Strip apostrophes and dots, keep hyphens for now
    s = re.sub(r"['\"\.]", "", s)
    return s


def extract_surname_candidates(full_name: str | None) -> list[str]:
    """Extract ordered candidate surname strings from full_name or short name.

    For 'Kevin Winston Jr.' -> ['Winston']
    For 'Steven Van Buren' -> ['Van Buren', 'Buren']
    For 'Dani Dennis-Sutton' -> ['Dennis-Sutton', 'Sutton']
    For 'R Mason Thomas' -> ['Mason Thomas', 'Thomas']
    For 'Patrick Mahomes' -> ['Mahomes']
    For 'Mahomes' -> ['Mahomes']
    """
    if not full_name:
        return []
    cleaned = drop_suffixes(full_name.strip())
    parts = cleaned.split()
    if not parts:
        return []
    if len(parts) == 1:
        return [parts[0]]

    candidates = []
    # Candidate 1: multi-word last name (all tokens after first name)
    c_multi = " ".join(parts[1:])
    candidates.append(c_multi)

    # Candidate 2: last token only (if different)
    c_last = parts[-1]
    if c_last != c_multi:
        candidates.append(c_last)

    # Candidate 3: second part of hyphenated surname (e.g. 'Sutton' from 'Dennis-Sutton')
    if "-" in c_last:
        h_parts = c_last.split("-")
        if len(h_parts) > 1 and h_parts[-1] and h_parts[-1] not in candidates:
            candidates.append(h_parts[-1])

    return candidates


def commentary_id(full_name: str | None, lookup: dict[str, int] | None = None) -> int | None:
    """Look up commentary ID for a player's full_name or surname.

    Returns the integer commentary ID if matched, else None.
    """
    if not full_name:
        return None

    if lookup is None:
        lookup = load_commentary_lookup()

    for cand in extract_surname_candidates(full_name):
        norm = normalize(cand)
        if not norm:
            continue

        # 1. Exact normalized match (e.g. 'smith', 'van buren', 'dennis-sutton')
        if norm in lookup:
            return lookup[norm]

        # 2. Stripped hyphen match (e.g. 'dennissutton')
        no_hyphen = norm.replace("-", "").replace(" ", "")
        if no_hyphen in lookup:
            return lookup[no_hyphen]

        # 3. Alphanumeric only
        alphanumeric = re.sub(r"[^a-z0-9]", "", norm)
        if alphanumeric in lookup:
            return lookup[alphanumeric]

    return None


# ------------------------------------------------------------- string distance ----
def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    s1, s2 = s1.lower(), s2.lower()
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            insertions = prev[j + 1] + 1
            deletions = curr[j] + 1
            substitutions = prev[j] + (0 if c1 == c2 else 1)
            curr[j + 1] = min(insertions, deletions, substitutions)
        prev = curr
    return prev[-1]


# -------------------------------------------------------- candidate suggestions ----
def _get_double_metaphone(word: str) -> tuple[str, str]:
    if metaphone is not None:
        p, s = metaphone.doublemetaphone(word)
        return p or "", s or ""
    # Fallback if metaphone library is somehow unavailable
    return word[:4].upper(), ""


def suggest_surnames(
    target_surname: str,
    team_surnames: set[str] | None = None,
    top_n: int = 5,
    candidates: list[str] | None = None,
) -> list[str]:
    """Suggest top N recorded surnames ranked by phonetic sound then edit distance.

    Filters out surnames already on team_surnames.
    Prefers same first letter.
    """
    if not target_surname:
        return []

    global _RECORDED_METADATA
    if _RECORDED_METADATA is None and candidates is None:
        load_commentary_lookup()

    team_normalized = {normalize(s) for s in (team_surnames or set()) if s}
    t_clean = normalize(target_surname)
    t_p, t_s = _get_double_metaphone(target_surname)
    t_first = target_surname[0].lower() if target_surname else ""

    if candidates is not None or _RECORDED_METADATA is None:
        cands = candidates if candidates is not None else get_recorded_surnames()
        meta = [(c, normalize(c), _get_double_metaphone(c)[0], _get_double_metaphone(c)[1], c[0].lower() if c else "")
                for c in cands]
    else:
        meta = _RECORDED_METADATA

    scored: list[tuple[tuple, str]] = []
    for cand, c_norm, c_p, c_s, c_first in meta:
        if c_norm in team_normalized:
            continue

        # Phonetic match tier
        # 0: exact primary match
        # 1: primary-secondary cross match
        # 2: secondary-secondary match
        # 3: metaphone edit distance
        if t_p and t_p == c_p:
            phonetic_tier = 0
            phone_dist = 0
        elif (t_p and t_p == c_s) or (t_s and t_s == c_p):
            phonetic_tier = 1
            phone_dist = 0
        elif t_s and c_s and t_s == c_s:
            phonetic_tier = 2
            phone_dist = 0
        else:
            phonetic_tier = 3
            d1 = levenshtein_distance(t_p, c_p) if t_p and c_p else 10
            d2 = levenshtein_distance(t_p, c_s) if t_p and c_s else 10
            phone_dist = min(d1, d2)

        same_first = 0 if (c_first and t_first and c_first == t_first) else 1
        ortho_dist = levenshtein_distance(t_clean, c_norm)
        len_diff = abs(len(target_surname) - len(cand))

        # Sort priority:
        # 1. same first letter (0 before 1)
        # 2. phonetic tier (0, 1, 2, 3)
        # 3. phonetic key distance
        # 4. orthographic edit distance
        # 5. length difference
        # 6. candidate alphabetical tie-breaker
        key = (same_first, phonetic_tier, phone_dist, ortho_dist, len_diff, cand)
        scored.append((key, cand))

    scored.sort(key=lambda x: x[0])
    return [cand for _, cand in scored[:top_n]]


def build_renamed_names(
    original_full_name: str | None,
    original_player_name: str | None,
    chosen_surname: str,
) -> tuple[str, str]:
    """Replace the surname in original full_name and player_name with chosen_surname.

    Preserves first name, middle initial/name, and suffix if present.
    """
    chosen = chosen_surname.strip()

    # Extract suffix from full_name or player_name
    fn = (original_full_name or "").strip()
    pn = (original_player_name or "").strip()

    suffix = ""
    m_suf = SUFFIX_PATTERN.search(fn) or SUFFIX_PATTERN.search(pn)
    if m_suf:
        suffix = " " + m_suf.group(1).strip()

    fn_no_suf = drop_suffixes(fn)
    pn_no_suf = drop_suffixes(pn)

    # Full name reconstruction
    if fn_no_suf:
        parts = fn_no_suf.split()
        if len(parts) <= 1:
            new_full_name = f"{chosen}{suffix}"
        else:
            first_parts = " ".join(parts[:-1])
            new_full_name = f"{first_parts} {chosen}{suffix}"
    else:
        new_full_name = f"{chosen}{suffix}"

    # Short player_name reconstruction (e.g. 'J. Josephs' -> 'J. Joseph')
    if pn_no_suf:
        parts = pn_no_suf.split()
        if len(parts) <= 1:
            new_player_name = f"{chosen}{suffix}"
        else:
            first_parts = " ".join(parts[:-1])
            new_player_name = f"{first_parts} {chosen}{suffix}"
    else:
        # Fall back from new_full_name: 'Joshua Joseph' -> 'J. Joseph'
        parts = new_full_name.split()
        if len(parts) > 1:
            initial = parts[0][0].upper() + "."
            rest = " ".join(parts[1:])
            new_player_name = f"{initial} {rest}"
        else:
            new_player_name = new_full_name

    return new_full_name, new_player_name


# ------------------------------------------------------------- DB commands ----
def run_report(
    dsn: str = DEFAULT_DSN,
    out_csv: str | None = None,
    season: int | None = None,
    include_prospects: bool = False,
) -> dict:
    """Compute commentary coverage on active players and generate review CSV."""
    import psycopg

    lookup = load_commentary_lookup()
    recorded_surnames = get_recorded_surnames()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if season is None:
                cur.execute("SELECT MAX(season) FROM player_seasons;")
                season_row = cur.fetchone()
                season = season_row[0] if season_row and season_row[0] else 2036

            # Query active players
            cur.execute(
                """
                SELECT p.id, p.player_name, p.full_name, t.abbreviation as team,
                       ps.position, ps.overall_rating
                FROM player_seasons ps
                JOIN players p ON p.id = ps.player_id
                LEFT JOIN teams t ON t.id = ps.team_id
                WHERE ps.season = %s
                  AND p.retired_season IS NULL
                  AND ps.position != 'DEF'
                ORDER BY ps.overall_rating DESC NULLS LAST, p.full_name;
                """,
                (season,),
            )
            players = cur.fetchall()

            # Roster surnames per team to avoid collisions
            team_surnames: dict[str, set[str]] = defaultdict(set)
            for _, _, full_name, team, _, _ in players:
                if team and full_name:
                    cands = extract_surname_candidates(full_name)
                    if cands:
                        team_surnames[team].add(cands[-1])

    total = len(players)
    matched = 0
    unmatched_rows: list[dict] = []

    for pid, p_name, f_name, team, pos, ovr in players:
        display_name = f_name or p_name or ""
        cid = commentary_id(display_name, lookup=lookup)
        if cid is not None:
            matched += 1
        else:
            cands = extract_surname_candidates(display_name)
            target_surname = cands[-1] if cands else display_name
            teammates = team_surnames.get(team or "", set())
            suggestions = suggest_surnames(
                target_surname,
                team_surnames=teammates,
                top_n=5,
                candidates=recorded_surnames,
            )
            # pad suggestions to 5
            while len(suggestions) < 5:
                suggestions.append("")

            unmatched_rows.append(
                {
                    "player_id": pid,
                    "name": display_name,
                    "team": team or "",
                    "pos": pos or "",
                    "ovr": ovr if ovr is not None else "",
                    "candidate_1": suggestions[0],
                    "candidate_2": suggestions[1],
                    "candidate_3": suggestions[2],
                    "candidate_4": suggestions[3],
                    "candidate_5": suggestions[4],
                    "chosen": "",
                }
            )

    unmatched_count = total - matched
    coverage_pct = (matched / total * 100) if total else 0.0

    print(f"Season {season} Active Players Commentary Report:")
    print(f"  Total Active Players (excl. DEF): {total}")
    print(f"  Commentary Matched:               {matched} ({coverage_pct:.1f}%)")
    print(f"  Unmatched (need rename):          {unmatched_count} ({100 - coverage_pct:.1f}%)")

    if out_csv:
        os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
        fieldnames = [
            "player_id",
            "name",
            "team",
            "pos",
            "ovr",
            "candidate_1",
            "candidate_2",
            "candidate_3",
            "candidate_4",
            "candidate_5",
            "chosen",
        ]
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(unmatched_rows)
        print(f"  Wrote {len(unmatched_rows)} unmatched players to {out_csv}")

    return {
        "season": season,
        "total": total,
        "matched": matched,
        "unmatched": unmatched_count,
        "coverage_pct": coverage_pct,
        "unmatched_rows": unmatched_rows,
    }


def run_apply(csv_path: str, dsn: str = DEFAULT_DSN, commit: bool = False) -> dict:
    """Apply approved renames from reviewed CSV to players and player_name_aliases."""
    import psycopg

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Reviewed CSV not found at '{csv_path}'")

    lookup = load_commentary_lookup()

    to_apply: list[dict] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chosen = (row.get("chosen") or "").strip()
            if chosen:
                to_apply.append(
                    {
                        "player_id": int(row["player_id"]),
                        "chosen": chosen,
                        "current_name": row.get("name", ""),
                    }
                )

    if not to_apply:
        print("No players with 'chosen' surname specified in CSV.")
        return {"applied": 0, "changes": []}

    changes = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for item in to_apply:
                pid = item["player_id"]
                chosen = item["chosen"]

                # Verify chosen surname exists in commentary lookup
                cid = commentary_id(chosen, lookup=lookup)
                if cid is None:
                    raise ValueError(f"Chosen surname '{chosen}' for player {pid} does not have a recorded commentary ID!")

                cur.execute("SELECT id, full_name, player_name FROM players WHERE id = %s;", (pid,))
                player = cur.fetchone()
                if not player:
                    print(f"Warning: Player ID {pid} not found in database; skipping.")
                    continue

                _, old_full, old_short = player
                new_full, new_short = build_renamed_names(old_full, old_short, chosen)

                # Record alias in player_name_aliases
                # Save both old full name and old short name if different
                aliases_to_record = {old_full, old_short} - {None, "", new_full, new_short}

                for alias in aliases_to_record:
                    cur.execute(
                        """
                        INSERT INTO player_name_aliases (player_id, alias, kind, created_at)
                        VALUES (%s, %s, 'commentary_rename', NOW())
                        ON CONFLICT (player_id, alias) DO NOTHING;
                        """,
                        (pid, alias),
                    )

                # Update players table
                cur.execute(
                    """
                    UPDATE players
                    SET full_name = %s, player_name = %s
                    WHERE id = %s;
                    """,
                    (new_full, new_short, pid),
                )

                changes.append(
                    {
                        "player_id": pid,
                        "old_full_name": old_full,
                        "new_full_name": new_full,
                        "old_player_name": old_short,
                        "new_player_name": new_short,
                        "commentary_id": cid,
                        "aliases_recorded": list(aliases_to_record),
                    }
                )

            if commit:
                conn.commit()
                print(f"Successfully committed {len(changes)} player rename(s) to database.")
            else:
                conn.rollback()
                print(f"[DRY RUN] Would rename {len(changes)} player(s). Rolled back without changes.")

    for ch in changes:
        print(f"  Player {ch['player_id']}: '{ch['old_full_name']}' -> '{ch['new_full_name']}' (Commentary ID {ch['commentary_id']})")

    return {"applied": len(changes), "committed": commit, "changes": changes}


# --------------------------------------------------------------------- CLI ----
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Madden Commentary Names & Renames Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # report command
    rep_parser = subparsers.add_parser("report", help="Generate commentary coverage report and review CSV")
    rep_parser.add_argument("--out", "-o", default=os.path.join(ROOT, "captures", "commentary_unmatched.csv"),
                            help="Output path for review CSV")
    rep_parser.add_argument("--season", "-s", type=int, default=None, help="Season to report (default: latest)")
    rep_parser.add_argument("--db", default=DEFAULT_DSN, help="Postgres connection string")
    rep_parser.add_argument("--prospects", action="store_true", help="Report on draft prospects")

    # apply command
    app_parser = subparsers.add_parser("apply", help="Apply reviewed surnames from CSV to players table")
    app_parser.add_argument("csv_file", help="Path to reviewed CSV with 'chosen' column filled")
    app_parser.add_argument("--commit", action="store_true", help="Commit changes to database (default: dry run)")
    app_parser.add_argument("--db", default=DEFAULT_DSN, help="Postgres connection string")

    args = parser.parse_args()

    if args.command == "report":
        run_report(dsn=args.db, out_csv=args.out, season=args.season, include_prospects=args.prospects)
    elif args.command == "apply":
        run_apply(csv_path=args.csv_file, dsn=args.db, commit=args.commit)


if __name__ == "__main__":
    main()
