"""Inferred values for no-evidence sliders of real coach and owner profiles
(docs/done/INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.2). Pure apart from reading the leans file.

A profile slider at 5 with no cited evidence is a *no-evidence* slider. Instead of reading as a flat 5 it gets
5 + the person's capped trait leans (newsroom/config/slider_leans.json) + a seeded step of -1/0/+1, kept in 2-8,
then the profile's effective total is balanced into 24-36 by moving inferred values toward 5. The values are
fixed in the file's `slider_inference` block at review time (tools.coach_profile_review infer), never touch an
evidenced slider, and reach the core through the reviewed lifecycle apply with a low-confidence, voice-only
`inferred:` provenance entry, so every display can mark them as inferred.

  load_leans(path, catalogue)          {trait: {slider: int}}; validates traits, sliders, -2..+2, <= 2 per trait
  neutral_sliders(doc)                 the no-evidence sliders, in SLIDERS order
  inference_draw(key, slider)          seeded uniform in [0, 1)
  infer_sliders(doc, leans, locked=)   {'version', 'values', 'basis'}
  check_inference(doc, leans)          messages for the doc's slider_inference block ([] = valid)
  effective_sliders(doc)               file sliders with the stored inferred values over them
  inferred_evidence(doc, slider)       the core evidence entry for one inferred (or JR-locked) slider
  is_inferred(entry)                   True for an `inferred:` provenance entry
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from newsroom import personas as legacy

INFERENCE_VERSION = 'slider-inference-v1'
INFERRED_SOURCE = 'inferred:' + INFERENCE_VERSION
JR_SET_SOURCE_PREFIX = 'jr-set:'
LEANS_FILE = 'newsroom/config/slider_leans.json'
LEAN_CAP = 2
INFERRED_RANGE = (2, 8)
JITTER_CUTS = (0.25, 0.75)
LEAN_RANGE = (-2, 2)
MAX_LEAN_SLIDERS = 2
BAND = (legacy.SLIDER_BUDGET_MIN, legacy.SLIDER_BUDGET_MAX)
FALLBACK_DATE = '2026-09-08'   # the profiles' source cutoff, for a file not yet reviewed

REPO_ROOT = Path(__file__).resolve().parent.parent
_LEANS_CACHE: dict = {}


class InferenceError(ValueError):
    pass


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def load_leans(path=LEANS_FILE, catalogue=None) -> dict:
    """{trait: {slider: lean}} from the leans file, validated against the trait catalogue."""
    p = Path(path)
    p = p if p.is_absolute() else REPO_ROOT / p
    doc = json.loads(p.read_text())
    if not isinstance(doc, dict) or doc.get('version') != INFERENCE_VERSION or not isinstance(doc.get('leans'), dict):
        raise InferenceError(f'{path}: needs version {INFERENCE_VERSION} and a leans object')
    catalogue = legacy.load_traits() if catalogue is None else catalogue
    out = {}
    for trait, leans in doc['leans'].items():
        if trait not in catalogue:
            raise InferenceError(f'lean for unknown trait {trait}')
        if not isinstance(leans, dict) or len(leans) > MAX_LEAN_SLIDERS:
            raise InferenceError(f'{trait}: leans must be an object with at most {MAX_LEAN_SLIDERS} sliders')
        for slider, lean in leans.items():
            if slider not in legacy.SLIDERS:
                raise InferenceError(f'{trait}: unknown slider {slider!r}')
            if type(lean) is not int or not LEAN_RANGE[0] <= lean <= LEAN_RANGE[1] or lean == 0:
                raise InferenceError(f'{trait}: lean for {slider} must be a nonzero integer -2..+2')
        out[trait] = dict(leans)
    return out


def _leans(leans):
    if leans is not None:
        return leans
    if 'default' not in _LEANS_CACHE:
        _LEANS_CACHE['default'] = load_leans()
    return _LEANS_CACHE['default']


def neutral_sliders(doc) -> list[str]:
    """Sliders at 5 that cite no evidence (a slider evidenced at 5 is not inferred)."""
    sliders = doc.get('sliders') or {}
    evidence = doc.get('slider_evidence') or {}
    return [s for s in legacy.SLIDERS if sliders.get(s) == 5 and s not in evidence]


def inference_draw(key, slider) -> float:
    digest = hashlib.sha256(json.dumps([INFERENCE_VERSION, key, slider]).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64


def _jitter(u):
    return -1 if u < JITTER_CUTS[0] else 1 if u >= JITTER_CUTS[1] else 0


def infer_sliders(doc, leans=None, *, locked=None) -> dict:
    """The inferred value of every no-evidence slider, with its basis. `locked` ({slider: value}) values are
    kept as given (JR's) and never balanced; evidenced sliders never move."""
    leans = _leans(leans)
    locked = dict(locked or {})
    traits = [t for t in (doc.get('traits') or []) if isinstance(t, str)]
    key = doc.get('key')
    values, basis = {}, {}
    for s in neutral_sliders(doc):
        if s in locked:
            values[s] = locked[s]
            basis[s] = {'locked': True}
            continue
        contrib = {t: leans[t][s] for t in traits if s in leans.get(t, {})}
        lean = _clamp(sum(contrib.values()), -LEAN_CAP, LEAN_CAP)
        jitter = _jitter(inference_draw(key, s))
        values[s] = _clamp(5 + lean + jitter, *INFERRED_RANGE)
        basis[s] = {'traits': contrib, 'lean': lean, 'jitter': jitter, 'balanced': 0}
    sliders = dict(doc.get('sliders') or {})
    movable = [s for s in values if s not in locked]

    def total():
        return sum(values.get(s, sliders.get(s, 0)) for s in legacy.SLIDERS)

    while True:
        t = total()
        if t > BAND[1]:
            pool = [s for s in movable if values[s] > 5]
            step = -1
        elif t < BAND[0]:
            pool = [s for s in movable if values[s] < 5]
            step = 1
        else:
            break
        if not pool:   # nothing left to move: check_inference reports the band
            break
        far = max(abs(values[s] - 5) for s in pool)
        s = next(x for x in pool if abs(values[x] - 5) == far)   # pool is in SLIDERS order
        values[s] += step
        basis[s]['balanced'] += 1
    return {'version': INFERENCE_VERSION, 'values': values, 'basis': basis}


def effective_sliders(doc) -> dict:
    """The file's sliders with the stored inferred values over the no-evidence ones."""
    sliders = dict(doc.get('sliders') or {})
    block = doc.get('slider_inference')
    if isinstance(block, dict) and isinstance(block.get('values'), dict):
        neutral = set(neutral_sliders(doc))
        for s, v in block['values'].items():
            if s in neutral:
                sliders[s] = v
    return sliders


def check_inference(doc, leans=None) -> list[str]:
    """§2.1 rules for the `slider_inference` block ([] = valid)."""
    block = doc.get('slider_inference')
    if not isinstance(block, dict):
        return ['slider_inference must be {version, values, locked}']
    errors = []
    unknown = sorted(set(block) - {'version', 'values', 'locked'})
    if unknown:
        errors.append(f'slider_inference has unknown fields {unknown}')
    if block.get('version') != INFERENCE_VERSION:
        errors.append(f'slider_inference version must be {INFERENCE_VERSION}')
    values = block.get('values')
    locked = block.get('locked', [])
    if not isinstance(values, dict):
        return errors + ['slider_inference values must be an object']
    if not isinstance(locked, list) or len(locked) != len(set(map(str, locked))):
        return errors + ['slider_inference locked must be a list of sliders without duplicates']
    neutral = neutral_sliders(doc)
    if set(values) != set(neutral):
        errors.append(f'slider_inference values must cover exactly the no-evidence sliders {neutral}, '
                      f'got {sorted(values)}')
    for s in locked:
        if s not in neutral:
            errors.append(f'locked slider {s!r} is not a no-evidence slider')
    for s, v in values.items():
        if type(v) is not int or not 0 <= v <= 10:
            errors.append(f'inferred {s} must be an integer 0-10')
    if errors:
        return errors
    expected = infer_sliders(doc, leans, locked={s: values[s] for s in locked})['values']
    for s in neutral:
        if s not in locked and values[s] != expected[s]:
            errors.append(f'inferred {s}={values[s]} is stale (inference gives {expected[s]}); re-run infer')
    total = sum(effective_sliders(doc).values())
    if not BAND[0] <= total <= BAND[1]:
        errors.append(f'effective slider total {total} is outside {BAND[0]}-{BAND[1]}')
    return errors


def inferred_evidence(doc, slider) -> dict:
    """Core evidence for an inferred slider: low confidence, voice-only provenance (no feature read changes);
    a slider JR locked carries a high-confidence `jr-set:` entry instead."""
    review = doc.get('review') or {}
    reviewed_at = review.get('reviewed_at') or FALLBACK_DATE
    block = doc.get('slider_inference') or {}
    if slider in (block.get('locked') or []):
        return dict(source=JR_SET_SOURCE_PREFIX + reviewed_at, date=reviewed_at, confidence='high', reviewer='JR',
                    reason='set by JR at review', features=['all'])
    leans = _leans(None)
    contrib = {t: leans[t][slider] for t in (doc.get('traits') or []) if slider in leans.get(t, {})}
    named = ', '.join(f'{t} {v:+d}' for t, v in contrib.items()) or 'no leaning traits'
    return dict(source=INFERRED_SOURCE, date=reviewed_at, confidence='low', reviewer=review.get('reviewer') or 'JR',
                reason=f'inferred from {named}; no direct evidence', features=['voice'])


def is_inferred(entry) -> bool:
    return isinstance(entry, dict) and str(entry.get('source', '')).startswith('inferred:')
