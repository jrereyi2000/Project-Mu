"""Retirement stage census (docs/in_progress/RETIREMENT_WINDOW_PLAN.md §2.9 step 1, U2).

Offline: reads reader snapshots (pipeline/save_reader/pc_save_reader.mjs JSON) in the order JR saved them and
counts, per snapshot, new retirees, un-retired players, players who vanished (active before, absent now: retired and
purged in one advance, or a free-agent purge) and purged retirees. It has no DSN flag, imports no database code,
opens no save and launches nothing; it writes only --out.

  python3 -m tools.retirement_stage_census A.json B.json ... [--out census.json]

Answers for JR's PC check (docs/reference/RETIREMENT_AUTHORITY.md §5): at which stops new `Retired` players appear,
whether any rostered player vanishes without first showing as `Retired` (the unrecoverable case), and at which
advance `Retired` rows disappear.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CENSUS_VERSION = 'retirement-stage-census-v1'
ACTIVE_STATUSES = ('Signed', 'FreeAgent', 'PracticeSquad')
FREE_AGENT_TEAM_INDEX = 32


def stop_of(snapshot: dict) -> str:
    """The league office stop name (FREE_AGENCY carries its wave), or '?' when the stage can't be told."""
    try:
        from pipeline.league_office import stage_and_wave  # lazy: only the stage table, no DB connection
        stage, wave = stage_and_wave(snapshot)
    except Exception:  # noqa: BLE001 - an unknown stage is reported, never fatal
        return '?'
    return f'{stage}:{wave}' if stage == 'FREE_AGENCY' and wave in (1, 2) else stage


def _image(snapshot: dict) -> dict[int, tuple]:
    out = {}
    for p in snapshot.get('players') or ():
        if p.get('pgid') is not None:
            out.setdefault(int(p['pgid']), (p.get('status'), p.get('team_index'),
                                             f'{p.get("first_name") or ""} {p.get("last_name") or ""}'.strip()))
    return out


def census(snapshots: list[dict], names: list[str] | None = None, stop_fn=stop_of) -> dict:
    """Per-snapshot counts and pgid lists, in the order given."""
    names = names or [f'#{i + 1}' for i in range(len(snapshots))]
    rows, prev = [], None
    for name, snap in zip(names, snapshots):
        img = _image(snap)
        season = snap.get('season') or {}
        row = {'snapshot': name, 'stop': stop_fn(snap), 'calendar_year': season.get('calendar_year'),
               'named_players': len(img), 'max_row': max((p.get('row') or 0 for p in snap.get('players') or ()),
                                                         default=None),
               'retired': sum(1 for v in img.values() if v[0] == 'Retired')}
        new, unretired, vanished, purged = [], [], [], []
        if prev is not None:
            for g, (status, team, nm) in sorted(img.items()):
                before = prev.get(g)
                if status == 'Retired' and (before is None or before[0] != 'Retired'):
                    new.append({'pgid': g, 'name': nm, 'before': list(before[:2]) if before else None})
                elif before and before[0] == 'Retired' and status in ACTIVE_STATUSES:
                    unretired.append({'pgid': g, 'name': nm, 'now': [status, team]})
            for g, (status, team, nm) in sorted(prev.items()):
                if g in img:
                    continue
                if status == 'Retired':
                    purged.append(g)
                elif status in ACTIVE_STATUSES:
                    kind = ('VANISHED_FREE_AGENT' if team in (None, FREE_AGENT_TEAM_INDEX)
                            else 'VANISHED_ROSTERED')
                    vanished.append({'pgid': g, 'name': nm, 'kind': kind, 'last': [status, team]})
        row.update(new_retirees=new, unretired=unretired, purged=purged,
                   vanished_rostered=[v for v in vanished if v['kind'] == 'VANISHED_ROSTERED'],
                   vanished_free_agent=[v for v in vanished if v['kind'] == 'VANISHED_FREE_AGENT'],
                   first=prev is None)
        rows.append(row)
        prev = img
    return {'version': CENSUS_VERSION, 'snapshots': rows, 'answers': {
        'stops_with_new_retirees': [r['stop'] for r in rows if r['new_retirees']],
        'stops_with_vanished_rostered': [r['stop'] for r in rows if r['vanished_rostered']],
        'stops_where_retired_purged': [r['stop'] for r in rows if r['purged']],
    }}


def table(result: dict) -> str:
    head = ('snapshot', 'stop', 'year', 'named', 'Retired', 'new', 'un-ret', 'purged', 'van.rost', 'van.FA')
    lines = [head]
    for r in result['snapshots']:
        dash = r['first']
        lines.append((r['snapshot'], r['stop'], str(r['calendar_year']), str(r['named_players']), str(r['retired']),
                      *(('-',) * 5 if dash else (str(len(r['new_retirees'])), str(len(r['unretired'])),
                                                 str(len(r['purged'])), str(len(r['vanished_rostered'])),
                                                 str(len(r['vanished_free_agent']))))))
    widths = [max(len(line[i]) for line in lines) for i in range(len(head))]
    out = ['  '.join(c.ljust(w) for c, w in zip(line, widths)).rstrip() for line in lines]
    a = result['answers']
    out.append('')
    out.append(f'New Retired at: {", ".join(a["stops_with_new_retirees"]) or "none"}')
    out.append(f'Rostered players vanished (unrecoverable) at: {", ".join(a["stops_with_vanished_rostered"]) or "none"}')
    out.append(f'Retired rows purged at: {", ".join(a["stops_where_retired_purged"]) or "none"}')
    return '\n'.join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python3 -m tools.retirement_stage_census',
                                 description='Per-stop retirement census from reader snapshots (offline).')
    ap.add_argument('snapshots', nargs='+', help='reader snapshot JSON files, in save order')
    ap.add_argument('--out', help='write the census JSON here')
    args = ap.parse_args(argv)
    if len(args.snapshots) < 2:
        print('need at least two snapshots, in save order', file=sys.stderr)
        return 2
    snaps = [json.loads(Path(p).read_text(encoding='utf-8')) for p in args.snapshots]
    result = census(snaps, [Path(p).name for p in args.snapshots])
    print(table(result))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
