"""tools/retirement_stage_census.py (RETIREMENT_WINDOW_PLAN.md §2.9 step 1, U2). Synthetic snapshots only."""
import ast
import inspect
import json

from tools import retirement_stage_census as rsc


def snap(players, offseason_stage=1, week=0, year=2027, stage='Offseason'):
    return {'season': {'stage': stage, 'offseason_stage': offseason_stage, 'week_index': week,
                       'calendar_year': year},
            'players': [{'pgid': g, 'row': i, 'first_name': 'P', 'last_name': str(g), 'status': s, 'team_index': t}
                        for i, (g, s, t) in enumerate(players)]}


A = snap([(1, 'Signed', 3), (2, 'Retired', 32), (3, 'Signed', 5), (4, 'FreeAgent', 32), (5, 'Signed', 7)])
B = snap([(1, 'Retired', 3), (2, 'Retired', 32), (4, 'FreeAgent', 32), (5, 'Signed', 7)], week=1)
C = snap([(1, 'FreeAgent', 32), (5, 'Signed', 7), (6, 'Signed', 9)], offseason_stage=1, week=2)


def test_census_counts():
    r = rsc.census([A, B, C], ['a', 'b', 'c'])
    a, b, c = r['snapshots']
    assert [x['stop'] for x in r['snapshots']] == ['OFFSEASON_START', 'RESIGN', 'FREE_AGENCY:1']
    assert a['first'] and a['retired'] == 1 and a['named_players'] == 5 and a['max_row'] == 4
    assert b['new_retirees'] == [{'pgid': 1, 'name': 'P 1', 'before': ['Signed', 3]}]
    assert b['vanished_rostered'] == [{'pgid': 3, 'name': 'P 3', 'kind': 'VANISHED_ROSTERED', 'last': ['Signed', 5]}]
    assert b['purged'] == [] and b['unretired'] == []
    assert c['unretired'] == [{'pgid': 1, 'name': 'P 1', 'now': ['FreeAgent', 32]}]
    assert c['purged'] == [2]
    assert c['vanished_free_agent'][0]['pgid'] == 4
    assert r['answers'] == {'stops_with_new_retirees': ['RESIGN'], 'stops_with_vanished_rostered': ['RESIGN'],
                            'stops_where_retired_purged': ['FREE_AGENCY:1']}


def test_unknown_stage_is_reported_not_fatal():
    r = rsc.census([snap([], stage='Mystery'), snap([])])
    assert r['snapshots'][0]['stop'] == '?'


def test_cli_table_and_json(tmp_path, capsys):
    paths = []
    for name, s in (('a.json', A), ('b.json', B), ('c.json', C)):
        p = tmp_path / name
        p.write_text(json.dumps(s))
        paths.append(str(p))
    out = tmp_path / 'out' / 'census.json'
    assert rsc.main(paths + ['--out', str(out)]) == 0
    text = capsys.readouterr().out
    assert 'b.json' in text and 'Rostered players vanished (unrecoverable) at: RESIGN' in text
    data = json.loads(out.read_text())
    assert data['version'] == rsc.CENSUS_VERSION and len(data['snapshots']) == 3
    assert rsc.main(paths[:1]) == 2


def test_offline_only():
    tree = ast.parse(inspect.getsource(rsc))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or '').split('.')[0])
    assert not mods & {'psycopg2', 'subprocess', 'db'}
    assert '--dsn' not in inspect.getsource(rsc)
