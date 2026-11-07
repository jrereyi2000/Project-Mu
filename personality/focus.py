"""Durable motivation and a refreshing current focus for real coaches and owners
(docs/in_progress/INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.1, §2.4). Pure apart from reading the templates file.

A v2 profile splits want/fear into `motivation` (durable, names nobody) and `focus` (per slot, may name up to two
people as subjects). The stored focus goes stale when a subject retires, leaves the team, leaves his seat or the
league, cannot be resolved, when the holder himself moves, or when `until_season` passes; the offseason run
(personality.focus_run) then drafts a new one from a template.

  validate_file_focus(doc)          motivation and focus rules for a v2 profile ([] = valid)
  stale_causes(row, facts, season)  [{'cause', 'subject'?}] for a stored focus row
  load_templates(path)              the validated template list
  focus_draw(universe, persona, season, slot)   seeded uniform in [0, 1)
  draft_focus(...)                  {'text', 'subjects', 'template_id', 'anchor'}
  change_note(old_row, new, causes, season)     one-sentence change note (<= 30 words)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

FOCUS_RULES_VERSION = 'focus-v1'
SLOTS = ('want', 'fear')
SUBJECT_KINDS = ('player', 'coach', 'gm')
SEATS = ('HC', 'OC', 'DC', 'OWNER')
DIRECTIONS = ('CONTEND', 'RETOOL', 'REBUILD')
MAX_MOTIVE_WORDS = 14
MAX_FOCUS_WORDS = 14
MAX_SUBJECTS = 2
MIN_UNTIL_SEASON = 2026
MAX_NOTE_WORDS = 30
TEMPLATES_FILE = 'newsroom/config/focus_templates.json'
STALE_CAUSES = ('SUBJECT_RETIRED', 'SUBJECT_LEFT_TEAM', 'SUBJECT_LEFT_ROLE', 'SUBJECT_LEFT_LEAGUE',
                'SUBJECT_UNRESOLVED', 'HOLDER_MOVED', 'EXPIRED')
STATUS_CAUSE = {'retired': 'SUBJECT_RETIRED', 'elsewhere': 'SUBJECT_LEFT_TEAM', 'not_in_seat': 'SUBJECT_LEFT_ROLE',
                'gone': 'SUBJECT_LEFT_LEAGUE'}
PEOPLE_KEYS = ('qb1', 'young_star', 'top_defender', 'top_weapon', 'head_coach', 'gm')
PLACEHOLDERS = PEOPLE_KEYS + ('team_name',)
WHEN_KEYS = ('direction', 'hot_seat', 'made_playoffs', 'won_super_bowl')
TEMPLATE_FIELDS = ('template_id', 'seats', 'slot', 'when', 'needs', 'traits_any', 'text')

REPO_ROOT = Path(__file__).resolve().parent.parent
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)*")
_PLACEHOLDER = re.compile(r'\{([a-z0-9_]+)\}')


class FocusError(ValueError):
    pass


def words(text) -> list[str]:
    return _WORD.findall(text) if isinstance(text, str) else []


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


# ------------------------------------------------------------------------------------------- file rules ---

def _subject_errors(subject, label):
    if not isinstance(subject, dict):
        return [f'{label} must be an object']
    kind = subject.get('kind')
    if kind not in SUBJECT_KINDS:
        return [f'{label} kind must be one of {list(SUBJECT_KINDS)}']
    if kind == 'coach':
        ref = subject.get('seed_ref')
        if set(subject) - {'kind', 'seed_ref'}:
            return [f'{label} coach subject is {{kind, seed_ref}}']
        if not isinstance(ref, dict) or not all(_nonempty(ref.get(k)) for k in ('table', 'team', 'role')):
            return [f'{label} coach subject needs a seed_ref {{table, team, role, name}}']
        return []
    if set(subject) - {'kind', 'name', 'team_2026'}:
        return [f'{label} {kind} subject is {{kind, name, team_2026}}']
    if not (_nonempty(subject.get('name')) and _nonempty(subject.get('team_2026'))):
        return [f'{label} {kind} subject needs name and team_2026']
    return []


def _person_name(target):
    if not isinstance(target, dict):
        return None
    if target.get('kind') == 'coach':
        return (target.get('seed_ref') or {}).get('name')
    return target.get('name')


def _named(text, name):
    """True when text contains the person's full name or surname (whole words, case-insensitive)."""
    if not (_nonempty(text) and _nonempty(name)):
        return False
    low = ' ' + ' '.join(w.lower() for w in words(text)) + ' '
    parts = [w.lower() for w in words(name)]
    if not parts:
        return False
    suffixes = {'jr', 'sr', 'ii', 'iii', 'iv'}
    core = [p for p in parts if p not in suffixes] or parts
    return (' ' + ' '.join(parts) + ' ') in low or (' ' + core[-1] + ' ') in low


def validate_file_focus(doc) -> list[str]:
    """§2.1: motivation.want/fear nonempty, <= MAX_MOTIVE_WORDS words, naming no focus subject or tie target;
    focus.want/fear null or {text, subjects, until_season}."""
    errors = []
    motivation = doc.get('motivation')
    focus = doc.get('focus')
    if not isinstance(motivation, dict) or set(motivation) != set(SLOTS):
        errors.append('motivation must be {want, fear}')
        motivation = {}
    if not isinstance(focus, dict) or set(focus) != set(SLOTS):
        errors.append('focus must be {want, fear}, each null or {text, subjects, until_season}')
        focus = {}
    names = []
    for slot in SLOTS:
        entry = focus.get(slot)
        label = f'focus.{slot}'
        if entry is None:
            continue
        if not isinstance(entry, dict) or set(entry) != {'text', 'subjects', 'until_season'}:
            errors.append(f'{label} must be null or {{text, subjects, until_season}}')
            continue
        n = len(words(entry.get('text')))
        if not _nonempty(entry.get('text')) or n > MAX_FOCUS_WORDS:
            errors.append(f'{label} text must be 1-{MAX_FOCUS_WORDS} words, got {n}')
        subjects = entry.get('subjects')
        if not isinstance(subjects, list) or len(subjects) > MAX_SUBJECTS:
            errors.append(f'{label} subjects must be a list of at most {MAX_SUBJECTS}')
            subjects = []
        for i, sub in enumerate(subjects):
            errs = _subject_errors(sub, f'{label}.subjects[{i}]')
            errors.extend(errs)
            if not errs:
                if sub['kind'] == 'coach':
                    ref = sub['seed_ref']
                    if (ref.get('team'), ref.get('role')) == (doc.get('team'), doc.get('role')):
                        errors.append(f'{label}.subjects[{i}] names the holder himself')
                names.append(_person_name(sub))
        until = entry.get('until_season')
        if until is not None and (type(until) is not int or until < MIN_UNTIL_SEASON):
            errors.append(f'{label} until_season must be null or a season >= {MIN_UNTIL_SEASON}')
    for tie in doc.get('ties') or []:
        if isinstance(tie, dict):
            names.append(_person_name(tie.get('target')))
    for slot in SLOTS:
        text = motivation.get(slot)
        label = f'motivation.{slot}'
        if not _nonempty(text):
            if motivation:
                errors.append(f'{label} must be nonempty')
            continue
        n = len(words(text))
        if n > MAX_MOTIVE_WORDS:
            errors.append(f'{label} must be at most {MAX_MOTIVE_WORDS} words, got {n}')
        for name in names:
            if _named(text, name):
                errors.append(f'{label} names {name}; people belong in the focus, not the motivation')
                break
    return errors


# ------------------------------------------------------------------------------------------- staleness ---

def subject_key(binding) -> str | None:
    if not isinstance(binding, dict) or not binding.get('namespace') or binding.get('external_id') in (None, ''):
        return None
    return f"{binding['namespace']}:{binding['external_id']}"


def stale_causes(row, facts, season) -> list[dict]:
    """Why a stored focus row no longer fits. `unknown` statuses and None facts are never stale."""
    if not facts:
        return []
    out = []
    statuses = facts.get('subject_status') or {}
    for sub in row.get('subjects') or []:
        key = subject_key(sub.get('binding'))
        name = sub.get('name')
        if key is None:
            out.append({'cause': 'SUBJECT_UNRESOLVED', 'subject': name})
            continue
        status = (statuses.get(key) or {}).get('status')
        if status in STATUS_CAUSE:
            out.append({'cause': STATUS_CAUSE[status], 'subject': name})
    anchor = row.get('anchor') or {}
    for k in ('team', 'seat'):
        now, then = facts.get(k), anchor.get(k)
        if now is not None and then is not None and now != then:
            out.append({'cause': 'HOLDER_MOVED'})
            break
    until = row.get('until_season')
    if until is not None and until < season:
        out.append({'cause': 'EXPIRED'})
    return out


# ------------------------------------------------------------------------------------------- templates ---

def _fill(text, values):
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def load_templates(path=TEMPLATES_FILE) -> list[dict]:
    p = Path(path)
    p = p if p.is_absolute() else REPO_ROOT / p
    doc = json.loads(p.read_text())
    if not isinstance(doc, dict) or doc.get('version') != FOCUS_RULES_VERSION or not isinstance(doc.get('templates'), list):
        raise FocusError(f'{path}: needs version {FOCUS_RULES_VERSION} and a templates list')
    out, seen = [], set()
    longest = {k: 'Aaaa Bbbb Cccc' for k in PEOPLE_KEYS}
    longest['team_name'] = 'Commanders'
    for t in doc['templates']:
        tid = t.get('template_id') if isinstance(t, dict) else None
        if not _nonempty(tid) or tid in seen:
            raise FocusError(f'template {tid!r}: missing or duplicate template_id')
        seen.add(tid)
        if set(t) - set(TEMPLATE_FIELDS):
            raise FocusError(f'{tid}: unknown fields {sorted(set(t) - set(TEMPLATE_FIELDS))}')
        seats, slot, when = t.get('seats'), t.get('slot'), t.get('when', {})
        needs, traits_any = t.get('needs', []), t.get('traits_any', [])
        if not isinstance(seats, list) or not seats or set(seats) - set(SEATS):
            raise FocusError(f'{tid}: seats must be from {list(SEATS)}')
        if slot not in SLOTS:
            raise FocusError(f'{tid}: slot must be want or fear')
        if not isinstance(when, dict) or set(when) - set(WHEN_KEYS):
            raise FocusError(f'{tid}: when keys must be from {list(WHEN_KEYS)}')
        if 'direction' in when and when['direction'] not in DIRECTIONS:
            raise FocusError(f'{tid}: direction must be one of {list(DIRECTIONS)}')
        if any(k != 'direction' and type(v) is not bool for k, v in when.items()):
            raise FocusError(f'{tid}: when flags must be true or false')
        if not isinstance(needs, list) or set(needs) - set(PEOPLE_KEYS):
            raise FocusError(f'{tid}: needs must be from {list(PEOPLE_KEYS)}')
        if not isinstance(traits_any, list):
            raise FocusError(f'{tid}: traits_any must be a list')
        text = t.get('text')
        if not _nonempty(text):
            raise FocusError(f'{tid}: needs text')
        used = set(_PLACEHOLDER.findall(text))
        if used - set(PLACEHOLDERS):
            raise FocusError(f'{tid}: unknown placeholders {sorted(used - set(PLACEHOLDERS))}')
        if (used - {'team_name'}) - set(needs):
            raise FocusError(f'{tid}: placeholders {sorted((used - {"team_name"}) - set(needs))} are not in needs')
        if len(words(_fill(text, longest))) > MAX_FOCUS_WORDS:
            raise FocusError(f'{tid}: filled text exceeds {MAX_FOCUS_WORDS} words')
        out.append(dict(template_id=tid, seats=list(seats), slot=slot, when=dict(when), needs=list(needs),
                        traits_any=list(traits_any), text=text))
    for seat in SEATS:
        for slot in SLOTS:
            gid = f'GENERIC_{seat}_{slot.upper()}'
            g = next((t for t in out if t['template_id'] == gid), None)
            if g is None or g['when'] or g['needs'] or seat not in g['seats'] or g['slot'] != slot:
                raise FocusError(f'missing generic template {gid} (no when, no needs)')
    return out


def focus_draw(universe_id, persona_id, season, slot) -> float:
    digest = hashlib.sha256(json.dumps([FOCUS_RULES_VERSION, universe_id, persona_id, season, slot])
                            .encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64


def _eligible(t, seat, slot, facts):
    if seat not in t['seats'] or t['slot'] != slot:
        return False
    for k, v in t['when'].items():
        if facts.get(k) is None or facts.get(k) != v:
            return False
    people = facts.get('people') or {}
    return all(people.get(k) for k in t['needs'])


def draft_focus(*, universe_id, persona_id, season, slot, facts, traits, templates) -> dict:
    """Pick a template by the seeded draw over the eligible ones (sorted by id; a template whose traits_any
    meets the traits counts twice) and fill it from the facts' people."""
    seat = facts.get('seat') or ('OWNER' if facts.get('role') == 'owner' else None)
    eligible = sorted((t for t in templates if _eligible(t, seat, slot, facts)), key=lambda t: t['template_id'])
    if not eligible:
        raise FocusError(f'no template for seat {seat!r} slot {slot!r}')
    traits = set(traits or [])
    pool = []
    for t in eligible:
        pool.extend([t, t] if traits & set(t['traits_any']) else [t])
    t = pool[int(focus_draw(universe_id, persona_id, season, slot) * len(pool))]
    people = facts.get('people') or {}
    values = {k: (people.get(k) or {}).get('name', '') for k in PEOPLE_KEYS}
    values['team_name'] = facts.get('team_name') or 'his team'
    used = [k for k in PEOPLE_KEYS if '{' + k + '}' in t['text']]
    subjects = [dict(kind=people[k]['kind'], name=people[k]['name'], binding=dict(people[k].get('binding') or {}) or None)
                for k in used]
    return dict(text=_fill(t['text'], values), subjects=subjects, template_id=t['template_id'],
                anchor=dict(team=facts.get('team'), seat=seat))


# ----------------------------------------------------------------------------------------- change note ---

def _cause_phrase(c):
    name = c.get('subject') or 'a key figure'
    return {
        'SUBJECT_RETIRED': f'with {name} retired',
        'SUBJECT_LEFT_TEAM': f'with {name} gone to another team',
        'SUBJECT_LEFT_ROLE': f'with {name} out of his old seat',
        'SUBJECT_LEFT_LEAGUE': f'with {name} out of the league',
        'SUBJECT_UNRESOLVED': f'with {name} not in the picture',
        'HOLDER_MOVED': 'in his new job',
        'EXPIRED': 'with that chapter closed',
    }.get(c.get('cause'), 'with things changed')


def change_note(old_row, new, causes, season) -> str:
    """'2031: with Lamar Jackson retired, he now wants a title with Tyler Shough in his prime.'"""
    slot = (old_row or {}).get('slot') or new.get('slot') or 'want'
    verb = 'wants' if slot == 'want' else 'fears'
    lead = _cause_phrase(causes[0]) if causes else 'with things changed'
    note = f'{season}: {lead}, he now {verb} {new["text"].rstrip(".")}.'
    w = note.split()
    if len(w) > MAX_NOTE_WORDS:
        note = ' '.join(w[:MAX_NOTE_WORDS]).rstrip('.,;') + '.'
    return note
