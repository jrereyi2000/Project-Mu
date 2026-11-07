"""Slider inference (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.2, §4). Pure, offline."""
import json

import pytest

from personality import core
from personality import slider_inference as si

LEANS = si.load_leans()


def doc(**over):
    d = {
        'key': 'coach:BAL:HC',
        'sliders': {'temperament': 5, 'sociability': 5, 'confidence': 7, 'openness': 5, 'warmth': 5, 'drive': 8},
        'slider_evidence': {'confidence': ['e1'], 'drive': ['e2']},
        'traits': ['LOYAL', 'RING_CHASER', 'GUARDED'],
        'review': {'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-07'},
    }
    d.update(over)
    return d


def with_block(d, locked=None):
    out = si.infer_sliders(d, LEANS, locked=locked)
    d = dict(d)
    d['slider_inference'] = {'version': si.INFERENCE_VERSION, 'values': out['values'], 'locked': sorted(locked or {})}
    return d


def write_leans(tmp_path, leans):
    p = tmp_path / 'leans.json'
    p.write_text(json.dumps({'version': si.INFERENCE_VERSION, 'leans': leans}))
    return p


def test_leans_file_loads_and_matches_plan_table():
    assert LEANS['ANALYTICS_BELIEVER'] == {'openness': 2}
    assert LEANS['PATIENT_BUILDER'] == {'temperament': -2}
    assert LEANS['CAR_GUY'] == {}
    assert all(len(v) <= 2 for v in LEANS.values())


@pytest.mark.parametrize('leans', [
    {'NOT_A_TRAIT': {'drive': 1}},
    {'LOYAL': {'charisma': 1}},
    {'LOYAL': {'warmth': 3}},
    {'LOYAL': {'warmth': 0}},
    {'LOYAL': {'warmth': 1, 'drive': 1, 'openness': 1}},
])
def test_bad_leans_rejected(tmp_path, leans):
    with pytest.raises(si.InferenceError):
        si.load_leans(write_leans(tmp_path, leans))


def test_only_no_evidence_sliders_inferred():
    d = doc(slider_evidence={'confidence': ['e1'], 'drive': ['e2'], 'warmth': ['e3']})
    assert si.neutral_sliders(d) == ['temperament', 'sociability', 'openness']
    assert set(si.infer_sliders(d, LEANS)['values']) == {'temperament', 'sociability', 'openness'}


def test_deterministic_and_key_dependent():
    a = si.infer_sliders(doc(), LEANS)
    assert a == si.infer_sliders(doc(), LEANS)
    draws = {si.inference_draw(f'coach:T{i}:HC', 'warmth') for i in range(20)}
    assert len(draws) == 20
    vecs = {tuple(sorted(si.infer_sliders(doc(key=f'coach:T{i}:HC'), LEANS)['values'].items())) for i in range(20)}
    assert len(vecs) > 1


def test_lean_cap_and_range():
    d = doc(traits=['LOYAL', 'PLAYERS_COACH', 'MENTOR'], sliders=dict(doc()['sliders'], confidence=5, drive=5),
            slider_evidence={})
    out = si.infer_sliders(d, LEANS)
    assert out['basis']['warmth']['lean'] == si.LEAN_CAP
    assert all(2 <= v <= 8 for v in out['values'].values())
    for i in range(50):
        vals = si.infer_sliders(dict(d, key=f'k{i}'), LEANS)['values']
        assert all(si.INFERRED_RANGE[0] <= v <= si.INFERRED_RANGE[1] for v in vals.values())


UP = {'A': {'temperament': 2, 'sociability': 2}, 'B': {'openness': 2, 'warmth': 2}}


def high_doc():
    sliders = {'temperament': 5, 'sociability': 5, 'confidence': 6, 'openness': 5, 'warmth': 5, 'drive': 6}
    return doc(sliders=sliders, traits=['A', 'B'], slider_evidence={'confidence': ['e'], 'drive': ['e']})


def test_band_balancing_moves_only_unlocked_inferred():
    d = high_doc()
    out = si.infer_sliders(d, UP)
    assert sum(out['values'].values()) + 12 == si.BAND[1]
    assert sum(b['balanced'] for b in out['basis'].values()) > 0
    locked = si.infer_sliders(d, UP, locked={'warmth': 8})
    assert locked['values']['warmth'] == 8 and locked['basis']['warmth'] == {'locked': True}
    assert sum(locked['values'].values()) + 12 == si.BAND[1]


def test_balancing_picks_farthest_then_slider_order():
    d = high_doc()
    raw = {s: min(8, 7 + si._jitter(si.inference_draw(d['key'], s))) for s in si.neutral_sliders(d)}
    expect = dict(raw)
    while sum(expect.values()) + 12 > 36:
        far = max(abs(v - 5) for v in expect.values() if v > 5)
        s = next(x for x in si.neutral_sliders(d) if expect[x] > 5 and abs(expect[x] - 5) == far)
        expect[s] -= 1
    assert si.infer_sliders(d, UP)['values'] == expect


def test_balancing_raises_low_totals():
    sliders = {'temperament': 5, 'sociability': 5, 'confidence': 2, 'openness': 5, 'warmth': 5, 'drive': 2}
    down = {'A': {'temperament': -2, 'sociability': -2}, 'B': {'openness': -2, 'warmth': -2}}
    d = doc(sliders=sliders, traits=['A', 'B'], slider_evidence={'confidence': ['e'], 'drive': ['e']})
    out = si.infer_sliders(d, down)
    assert sum(out['values'].values()) + 4 == si.BAND[0]


def test_check_inference_valid_stale_and_locked():
    d = with_block(doc())
    assert si.check_inference(d, LEANS) == []
    edited = dict(d, traits=['OLD_SCHOOL', 'GRUDGE_HOLDER', 'DISCIPLINARIAN'])
    errs = si.check_inference(edited, LEANS)
    assert any('stale' in e for e in errs)
    locked = with_block(doc(), locked={'warmth': 9})
    assert si.check_inference(locked, LEANS) == []
    bad = dict(d, slider_inference=dict(d['slider_inference'], locked=['drive']))
    assert any('not a no-evidence slider' in e for e in si.check_inference(bad, LEANS))
    missing = dict(d, slider_inference=dict(d['slider_inference'], values={'warmth': 5}))
    assert any('cover exactly' in e for e in si.check_inference(missing, LEANS))
    assert si.check_inference(doc(), LEANS) == ['slider_inference must be {version, values, locked}']


def test_effective_sliders_overlay_only_neutral():
    d = with_block(doc())
    eff = si.effective_sliders(d)
    assert eff['confidence'] == 7 and eff['drive'] == 8
    for s, v in d['slider_inference']['values'].items():
        assert eff[s] == v
    assert si.effective_sliders(doc()) == doc()['sliders']


def test_inferred_evidence_passes_core_validation():
    d = with_block(doc(), locked={'sociability': 3})
    ev = {'slider:' + s: si.inferred_evidence(d, s) for s in d['slider_inference']['values']}
    assert si.is_inferred(ev['slider:warmth'])
    assert ev['slider:warmth']['features'] == ['voice'] and ev['slider:warmth']['confidence'] == 'low'
    assert 'LOYAL +2' in ev['slider:warmth']['reason']
    assert not si.is_inferred(ev['slider:sociability'])
    assert ev['slider:sociability']['source'] == 'jr-set:2026-11-07'
    core.initialize(core.Identity('t', 'coach', 'x'), 'real', 2026, sliders=si.effective_sliders(d),
                    traits=list(d['traits']), evidence=ev)


def test_is_inferred():
    assert si.is_inferred({'source': 'inferred:slider-inference-v1'})
    assert not si.is_inferred({'source': 'https://example.com'})
    assert not si.is_inferred(None)
