"""tools/player_trade_request_preview.py (PERSONALITY_TRADE_REQUESTS_PLAN.md §2.7, U3): byte-identical reports,
disabled neutral equality, shadow without external writes, malformed/out-of-order input, the 2026-2035 example
with ALLUSER/user_teams authority, an approved deal that never counts as a trade, and the contract handoff
contract test (one incident through relationship + request + trait context is charged once; the request layer adds
zero utility). The handoff test uses a stub consumer: it is not production contract integration."""
from copy import deepcopy
import json

import pytest

from engines import player_trade_requests as tr
from tools import player_trade_request_preview as pv


def run_cli(tmp_path, data, *extra, name='out'):
    src = tmp_path / 'in.json'
    src.write_text(json.dumps(data))
    out = tmp_path / name
    code = pv.main(['--input', str(src), '--out', str(out), *extra])
    return code, out


def test_reports_are_byte_identical(tmp_path):
    data = pv.example_input()
    code1, a = run_cli(tmp_path, data, name='a')
    code2, b = run_cli(tmp_path, data, name='b')
    assert code1 == code2 == 0
    for f in ('report.json', 'README.md'):
        assert (a / f).read_bytes() == (b / f).read_bytes()
    assert pv.example_input() == data                     # the example itself is deterministic


def test_disabled_mode_is_neutral_and_equal_across_arms(tmp_path):
    code, out = run_cli(tmp_path, pv.example_input(), '--mode', 'disabled')
    assert code == 0
    report = json.loads((out / 'report.json').read_text())
    assert report['mode'] == 'disabled'
    for p in report['players']:
        assert p['arms_equal']
        for row in p['arms']['personality']['timeline']:
            assert row.get('outcome') in ('NOOP', 'DISABLED')
        assert p['arms']['personality']['annual'] == {} and p['arms']['personality']['gm_signal'] is None


def test_shadow_writes_only_the_two_report_files(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://should-not-be-read/madden')
    code, out = run_cli(tmp_path, pv.example_input())
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ['README.md', 'report.json']
    source = open(pv.__file__, encoding='utf-8').read()
    assert 'psycopg' not in source and 'player_trade_request_store' not in source and 'environ' not in source


def test_example_spans_2026_to_2035_and_matches_the_plan(tmp_path):
    code, out = run_cli(tmp_path, pv.example_input())
    report = json.loads((out / 'report.json').read_text())
    players = {p['name']: p for p in report['players']}
    seasons = {row['season'] for p in report['players'] for row in p['arms']['personality']['timeline']}
    assert min(seasons) <= 2027 and max(seasons) >= 2035
    diva = players['benched_diva']['arms']
    assert diva['personality']['final_status'] == 'RESOLVED_TRADE'
    assert diva['personality']['public']['current'] is False             # disclosed ACTIVE, now dated
    assert diva['neutral']['final_status'] is None                       # matched control: no request
    assert players['broken_promise_repaired']['arms']['personality']['final_status'] == 'WITHDRAWN'
    calm = players['no_request_no_reroll']['arms']['personality']
    assert [r['outcome'] for r in calm['timeline']] == ['NO_REQUEST', 'NOOP', 'NOOP']
    jr = players['jr_team_2034']['arms']['personality']
    assert [r.get('outcome') for r in jr['timeline']] == ['REQUEST', 'APPLIED', 'REJECTED', 'EXISTING']
    assert jr['timeline'][2]['error'] == 'not_executed' and jr['final_status'] == 'ACTIVE'
    assert jr['front_office']['team_response'] == 'ACKNOWLEDGED'
    for p in report['players']:
        fo = p['arms']['personality']['front_office']
        if fo:
            assert not {'draw', 'probability', 'traits'} & set(fo)
    readme = (out / 'README.md').read_text()
    assert 'Synthetic demonstration only' in readme and 'every_eligible_requests' in readme


def test_allusers_flag_is_not_authority():
    data = pv.example_input()
    # Every team flagged by Madden as a user team changes nothing: authority comes from user_team_ids.
    step = data['players'][0]['steps'][2]
    assert step['context']['control']['authority'] == 'SIMULATED' and step['action']['actor'] == 'GM'
    jr_ctx = data['players'][3]['steps'][1]['context']['control']
    assert jr_ctx == {'team_id': 29, 'user_controlled': True, 'suggestions_on': False, 'authority': 'JR_RECORDED'}


@pytest.mark.parametrize('mutate,needle', [
    (lambda d: d.update(schema_version=2), 'schema_version'),
    (lambda d: d.update(mode='enabled'), 'ENABLED'),
    (lambda d: d['policy'].update(lower_probability=0.9), 'policy'),
    (lambda d: d['players'][0]['steps'].reverse(), 'out of order'),
    (lambda d: d['players'][0]['steps'][0]['clock'].update(window_kind='WEEKLY'), 'steps[0]'),
    (lambda d: d['players'].append(deepcopy(d['players'][0])), 'unique'),
    (lambda d: d['players'][0].update(steps=[]), 'at least one step'),
])
def test_malformed_input_fails_cleanly(tmp_path, capsys, mutate, needle):
    data = pv.example_input()
    mutate(data)
    code, out = run_cli(tmp_path, data)
    assert code == 2 and needle in capsys.readouterr().err and not out.exists()


def test_enabled_mode_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        run_cli(tmp_path, pv.example_input(), '--mode', 'enabled')
    with pytest.raises(pv.PreviewError):
        pv.load_input(pv.example_input(), mode='enabled')


def test_help_succeeds(capsys):
    with pytest.raises(SystemExit) as e:
        pv.main(['--help'])
    assert e.value.code == 0 and '--mode' in capsys.readouterr().out


# ------------------------------------------------------------------------------- contract handoff test ---

def stub_contract_consumer(relationship_breach_source_ids, request_context, traits):
    """Stand-in contract policy: charges each incident contribution once, keyed by its source IDs, whatever path
    (relationship breach, request context, GRUDGE_HOLDER read) it arrives by. Returns (charges, utility from the
    request layer)."""
    keys = set(relationship_breach_source_ids)
    keys |= set(request_context.get('source_event_ids', []))
    if 'GRUDGE_HOLDER' in traits:
        keys |= set(relationship_breach_source_ids)       # the trait reads the same incidents, never new ones
    request_utility = 0                                    # the request layer supplies context only
    return {k: 1 for k in sorted(keys)}, request_utility


def test_contract_handoff_charges_one_incident_once():
    lost = tr.Incident('inc:treatment:team:3:cut-1', ('cut-1',), 'TREATMENT', 3, tr.clock_ordinal(2027, 1),
                       tr.clock_ordinal(2027, 3), 2, 'MATERIAL', 'OPEN', (), 'PRIVATE')
    alias = tr.Incident('inc:treatment:team:3:cut-1', ('cut-1',), 'TREATMENT', 3, tr.clock_ordinal(2027, 1),
                        tr.clock_ordinal(2027, 3), 2, 'MATERIAL', 'OPEN', (), 'PRIVATE')
    c = tr.RequestClock('u', 2027, 'cp', tr.clock_ordinal(2027, 5), 'w', 'IN_SEASON', True, 's')
    for n in range(5000):
        pid = f'h-{n}'
        if tr.seeded_draw('u', pid, 2027) < 0.15:
            break
    view = {'status': 'stored', 'origin': 'generated', 'sliders': {}, 'traits': ['GRUDGE_HOLDER'], 'revision': 0,
            'persona_id': pid, 'view_version': 'views-v2'}
    ctx = tr.RequestContext(1, pid, 3, view, 'r', 'x', 'a', c.ordinal, c.ordinal, (lost,),
                            tr.control_context(3, user_team_ids=[], suggestions_on=True))
    d = tr.evaluate_request(ctx, c, None, None, tr.offline_illustration_policy(), mode='SHADOW')
    assert d.outcome == 'REQUEST'
    rc = tr.contract_request_context(d.after)
    assert 'refuse_extension' not in rc and 'utility' not in json.dumps(rc)
    charges, request_utility = stub_contract_consumer(list(alias.source_event_ids), rc, view['traits'])
    assert charges == {'cut-1': 1} and request_utility == 0
    with pytest.raises(tr.TradeRequestError, match='incident_conflict'):   # one source, two incidents: reconcile
        tr.validate_request_context(tr.RequestContext(1, pid, 3, view, 'r', 'x', 'a', c.ordinal, c.ordinal,
                                                      (lost, tr.Incident('inc:other', ('cut-1',), 'LOST_ROLE', 3,
                                                                         lost.first_ordinal, lost.last_ordinal, 4,
                                                                         'MATERIAL', 'OPEN', (), 'PRIVATE')),
                                                      ctx.control), c)


def test_report_trace_marks_zero_request_penalty(tmp_path):
    code, out = run_cli(tmp_path, pv.example_input())
    report = json.loads((out / 'report.json').read_text())
    for p in report['players']:
        for iid, row in p['arms']['personality']['contribution_trace'].items():
            assert row['contribution_key'] == iid and row['request_utility_penalty'] == 0
