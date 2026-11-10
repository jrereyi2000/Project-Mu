"""Personality evolution: candidates, the seeded draw, the one-a-season arbiter and change notes.

docs/done/PERSONALITY_EVOLUTION_PLAN.md §2.3. Pure: no database, clock or global random state. The run
(personality/evolution_run.py) reads the facts and profiles, calls these, then submits the chosen change through
personality.store.change, whose core checks (revision, annual slot, validity) stay final.

Candidate dict (also the persona_evolution_candidates row, plan §2.4):
  {candidate_id, universe_id, persona_type, persona_id, season, source ('RULE'|'RELATIONSHIPS'|'DEVELOPMENT'|
   'OTHER'), rule_id, rules_version, change {kind, slider/step | remove/add}, chance, draw, source_event_ids,
   note, priority, status, reason, core_event_id}
status: 'eligible' (passed the draw, before arbitration), then one of 'drawn_out', 'blocked', 'superseded',
'chosen' (the run records it as 'applied' once store.change accepts it).

External candidates (other features, plan S3) arrive as the relationships plan's review result plus who they are
for: {source, persona_type, persona_id, status, candidate: {event_id, source_event_id, season, rule_version, reason,
kind, add/remove/slider/step, supporting_source_ids, evidence?}, priority?}. Only status 'ready' joins; others are
ignored. The development plan (PERSONALITY_DEVELOPMENT_COACHING_PLAN.md §2.6) sends the same shape as source
'DEVELOPMENT' once it is built; until then `external` is simply empty.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from personality import core
from personality.evolution_rules import EVOLUTION_RULES_VERSION, RULES, fill_note

MAX_CHANGE_NOTES = 2
EVOLUTION_TICK = 24          # pipeline.league_office.STAGE_WEEKS['OFFSEASON_START']
EXTERNAL_SOURCES = ('RELATIONSHIPS', 'DEVELOPMENT', 'OTHER')
EXTERNAL_PRIORITY = 0        # an approved, reviewed external candidate outranks every rule (As built)
CHANGE_FIELDS = ('kind', 'slider', 'step', 'remove', 'add')
NOTE_OVERRIDE_PREFIX = 'note:'


def candidate_id(universe_id, persona_id, season, rule_id):
    return f'{universe_id}:{persona_id}:{season}:{rule_id}'


def draw(universe_id, persona_id, season, rule_id):
    """A replayable uniform number in [0, 1) for one person, season and rule."""
    key = json.dumps([EVOLUTION_RULES_VERSION, universe_id, persona_id, season, rule_id], separators=(',', ':'))
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big') / 2 ** 64


def _cited(facts, rule):
    ids = facts.get('source_event_ids') or {}
    out = []
    for fact in rule.facts:
        for sid in ids.get(fact) or ():
            if sid not in out:
                out.append(sid)
    return out


def rule_candidates(facts, rules=RULES):
    """Every rule whose `when` holds for this person, drawn: 'eligible' below its chance, else 'drawn_out'."""
    role, pid, universe, season = facts['role'], facts['persona_id'], facts['universe_id'], facts['season']
    out = []
    for rule in rules:
        if role not in rule.roles or not rule.when(facts):
            continue
        d = draw(universe, pid, season, rule.rule_id)
        out.append(dict(candidate_id=candidate_id(universe, pid, season, rule.rule_id), universe_id=universe,
                        persona_type=role, persona_id=pid, season=season, source='RULE', rule_id=rule.rule_id,
                        rules_version=EVOLUTION_RULES_VERSION, change=rule.change(facts), chance=rule.chance,
                        draw=d, source_event_ids=_cited(facts, rule), note=fill_note(rule, facts),
                        priority=rule.priority, status='eligible' if d < rule.chance else 'drawn_out',
                        reason=None if d < rule.chance else f'draw {d:.4f} >= chance {rule.chance:.2f}',
                        core_event_id=None))
    return out


def external_candidate(item, *, universe_id, season):
    """One external review result as a candidate dict, or None when it isn't 'ready' (ignored, plan §2.3)."""
    if not isinstance(item, dict) or item.get('status') != 'ready':
        return None
    cand = item['candidate']
    source = item.get('source') or 'OTHER'
    if source not in EXTERNAL_SOURCES:
        source = 'OTHER'
    ext_id = 'external:' + str(cand['event_id'])
    sids = [cand['source_event_id']] + [s for s in cand.get('supporting_source_ids') or ()
                                        if s != cand['source_event_id']]
    priority = item.get('priority')
    out = dict(candidate_id=candidate_id(universe_id, item['persona_id'], season, ext_id), universe_id=universe_id,
               persona_type=item['persona_type'], persona_id=item['persona_id'], season=season, source=source,
               rule_id=ext_id, rules_version=cand['rule_version'],
               change={k: cand[k] for k in CHANGE_FIELDS if cand.get(k) is not None},
               chance=None, draw=None, source_event_ids=sids, note=cand['reason'],
               priority=priority if type(priority) is int else EXTERNAL_PRIORITY, status='eligible', reason=None,
               core_event_id=None)
    if cand.get('evidence'):
        out['evidence'] = deepcopy(cand['evidence'])
    return out


def change_event(chosen, profile, season):
    """The store.change event for the chosen candidate (core.apply_change accepts it)."""
    if not chosen['source_event_ids']:
        raise core.PersonalityError('an evolution change must cite a source event')
    event = dict(event_id=f"evo:{chosen['universe_id']}:{chosen['persona_id']}:{season}",
                 source_event_id=chosen['source_event_ids'][0], season=season, tick=EVOLUTION_TICK,
                 expected_revision=profile['revision'], rule_version=chosen['rules_version'],
                 reason=chosen['note'])
    event.update({k: deepcopy(v) for k, v in chosen['change'].items() if k in CHANGE_FIELDS})
    if chosen.get('evidence'):
        event['evidence'] = deepcopy(chosen['evidence'])
    return event


def blocked_reason(cand, profile, season):
    """None when core validation would accept the change, else why not (the candidate is never forced)."""
    if profile is None:
        return 'no_profile'
    try:
        core.apply_change(deepcopy(profile), change_event(cand, profile, season))
    except core.ConflictError as e:
        return 'stale_revision' if 'revision' in str(e) else 'annual_slot_used'
    except core.PersonalityError as e:
        msg = str(e)
        if 'slider total' in msg:
            return 'slider_total_outside_band'
        if 'invalid six sliders' in msg:
            return 'slider_out_of_range'
        if 'unknown' in msg:
            return 'origin_unknown'
        return 'invalid_change: ' + msg
    except (KeyError, TypeError) as e:
        return f'invalid_change: {e}'
    return None


def _order(c):
    return (c['priority'], 0.0 if c['draw'] is None else c['draw'], c['rule_id'])


def arbitrate(candidates, external=(), *, season, profiles=None):
    """{persona_id: {'chosen': candidate | None, 'others': [candidates]}}, one change per person (plan D1).

    1. rule candidates keep their draw status; external candidates join only when 'ready';
    2. with `profiles` ({persona_id: profile}), a change core validation would refuse is 'blocked' (a person
       already changed this season is blocked 'annual_slot_used'; a person without a profile 'no_profile');
    3. the lowest priority wins, ties to the lowest draw (externals count as 0), then rule_id;
    4. the rest are 'superseded'.
    Inputs are not mutated."""
    people = {}
    for cand in candidates:
        people.setdefault(cand['persona_id'], []).append(deepcopy(cand))
    for item in external:
        item = item if isinstance(item, dict) else {}
        universe = item.get('universe_id') or next(
            (c['universe_id'] for c in candidates if c['persona_id'] == item.get('persona_id')), None)
        cand = external_candidate(item, universe_id=universe, season=season)
        if cand is not None:
            people.setdefault(cand['persona_id'], []).append(cand)
    result = {}
    for pid in sorted(people):
        rows = people[pid]
        live = [c for c in rows if c['status'] == 'eligible']
        if profiles is not None:
            for c in live:
                why = blocked_reason(c, profiles.get(pid), season)
                if why:
                    c['status'], c['reason'] = 'blocked', why
            live = [c for c in live if c['status'] == 'eligible']
        live.sort(key=_order)
        chosen = live[0] if live else None
        for c in live[1:]:
            c['status'], c['reason'] = 'superseded', f"superseded by {chosen['rule_id']}"
        if chosen is not None:
            chosen['status'] = 'chosen'
        result[pid] = {'chosen': chosen, 'others': [c for c in sorted(rows, key=_order) if c is not chosen]}
    return result


def _position(receipt):
    tick = receipt.get('effective_tick')
    return (receipt['season'], -1 if tick is None else tick, receipt.get('seq') or 0)


def change_notes(history, *, as_of, limit=MAX_CHANGE_NOTES):
    """The newest `limit` change notes from store.history receipts up to `as_of` (a season, or (season, tick)).

    Evolution receipts whose request carries a reason give their reason; JR overrides give a note only when the
    override reason starts with 'note:'. A note that doesn't name its season is prefixed '<season>: '."""
    cutoff = (as_of, 99) if isinstance(as_of, int) else (as_of[0], 99 if as_of[1] is None else as_of[1])
    notes = []
    for r in sorted(history, key=_position, reverse=True):
        if _position(r)[:2] > cutoff:
            continue
        request = r.get('request') or {}
        reason = request.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            continue
        if r.get('kind') == 'evolution':
            text = reason.strip()
        elif r.get('kind') == 'override' and reason.startswith(NOTE_OVERRIDE_PREFIX):
            text = reason[len(NOTE_OVERRIDE_PREFIX):].strip()
            if not text:
                continue
        else:
            continue
        if str(r['season']) not in text:
            text = f"{r['season']}: {text}"
        notes.append(text)
        if len(notes) >= limit:
            break
    return notes
