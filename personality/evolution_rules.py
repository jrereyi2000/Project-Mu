"""Versioned personality evolution rules (docs/in_progress/PERSONALITY_EVOLUTION_PLAN.md §2.2). Pure.

A rule turns one person's season facts (personality/evolution_facts.py) into at most one candidate change with a
chance and a priority. The seeded draw and the one-a-season arbiter live in personality/evolution.py; nothing here
reads a database, a clock or a random source. The chances are review-only starting points (plan §8 Q1): tune them
in a new rules version, never in place, so an old run's receipts keep meaning what they said.

Facts a rule reads are listed in its `facts`; a fact that is None (its source is missing, plan R3) never fires a
rule. No rule reads money, contracts or paydays (PERSONALITY_PLAN §6, plan S6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

EVOLUTION_RULES_VERSION = 'evolution-v1'

# awards.award_type values (newsroom/award_winners.py AWARD_KEYS, newsroom/all_pro.py team levels)
PLAYER_STAR_AWARDS = ('MVP', 'OPOY', 'DPOY', 'First Team All-Pro')
AWARD_LABELS = {'MVP': 'MVP', 'OPOY': 'Offensive Player of the Year', 'DPOY': 'Defensive Player of the Year',
                'First Team All-Pro': 'first-team All-Pro'}
COACH_OF_THE_YEAR = 'COTY'


@dataclass(frozen=True)
class Rule:
    rule_id: str
    roles: frozenset
    when: Callable[[dict], bool]
    change: Callable[[dict], dict]
    chance: float
    priority: int
    note: str
    facts: tuple          # the season facts the rule reads; their source IDs are the candidate's citations


def traits_of(facts):
    return set(((facts.get('profile_view') or {}).get('traits')) or ())


def _yes(facts, key):
    return facts.get(key) is True


def _no(facts, key):
    return facts.get(key) is False


def _at_least(facts, key, n):
    value = facts.get(key)
    return value is not None and value >= n


def _award(facts, kinds):
    return [a for a in (facts.get('awards') or ()) if a in kinds]


def _slider(name, step=1):
    return lambda facts: {'kind': 'slider', 'slider': name, 'step': step}


def _trait(remove=None, add=None):
    def change(facts):
        out = {'kind': 'trait'}
        if remove:
            out['remove'] = remove
        if add:
            out['add'] = add
        return out
    return change


RULES = (
    Rule('TITLE_LEGACY', frozenset({'player', 'coach'}),
         lambda f: (_yes(f, 'won_super_bowl') and 'RING_CHASER' in traits_of(f)
                    and _at_least(f, 'seasons_in_league', 4)),
         _trait(remove='RING_CHASER', add='LEGACY_BUILDER'), 0.35, 1,
         'Since the {season} title he talks more about legacy than rings.',
         ('won_super_bowl', 'seasons_in_league')),
    Rule('FIRED_TEMPER', frozenset({'coach'}),
         lambda f: _yes(f, 'coach_fired'),
         _slider('temperament'), 0.50, 1,
         'Since his {season} firing he has a shorter fuse.',
         ('coach_fired',)),
    Rule('MENTOR_RETIRES', frozenset({'player'}),
         lambda f: (_yes(f, 'mentor_retired') and _at_least(f, 'age', 27)
                    and not traits_of(f) & {'MENTOR', 'LONER'}),
         _trait(add='MENTOR'), 0.25, 2,
         'Since his own mentor retired he has taken the younger players under his wing.',
         ('mentor_retired', 'age')),
    Rule('VETERAN_LEADER', frozenset({'player'}),
         lambda f: (_at_least(f, 'age', 31) and _at_least(f, 'tenure_seasons_on_team', 5)
                    and not traits_of(f) & {'MENTOR', 'LONER'}),
         _trait(add='MENTOR'), 0.10, 4,
         'After {tenure_seasons_on_team} seasons on the team he has become a mentor to its young players.',
         ('age', 'tenure_seasons_on_team')),
    Rule('BENCHED_TEMPER', frozenset({'player'}),
         lambda f: _at_least(f, 'unsatisfied_role_weeks', 6),
         _slider('temperament'), 0.30, 2,
         'After {unsatisfied_role_weeks} weeks in {season} in a smaller role than he expected he is quicker to bristle.',
         ('unsatisfied_role_weeks',)),
    Rule('CUT_GRUDGE', frozenset({'player'}),
         lambda f: (_yes(f, 'released_by_team') and _yes(f, 'signed_elsewhere_after_release')
                    and 'PEACEMAKER' not in traits_of(f)),
         _trait(add='GRUDGE_HOLDER'), 0.15, 3,
         'He has not forgotten the team that cut him in {season}.',
         ('released_by_team', 'signed_elsewhere_after_release')),
    Rule('MVP_CONFIDENCE', frozenset({'player'}),
         lambda f: bool(_award(f, PLAYER_STAR_AWARDS)),
         _slider('confidence'), 0.40, 2,
         'Since his {award} season in {season} he carries himself with more certainty.',
         ('awards',)),
    Rule('LONG_INJURY_DRIVE', frozenset({'player'}),
         lambda f: _at_least(f, 'injury_weeks_out', 8),
         _slider('drive'), 0.20, 3,
         'Since the injury that cost him {injury_weeks_out} weeks in {season} he works with more urgency.',
         ('injury_weeks_out',)),
    Rule('PROMOTED_CONFIDENCE', frozenset({'coach'}),
         lambda f: _yes(f, 'coach_promoted_to_hc'),
         _slider('confidence'), 0.40, 2,
         'Since his promotion to head coach in {season} he speaks with more authority.',
         ('coach_promoted_to_hc',)),
    Rule('LOSING_OPENNESS', frozenset({'coach'}),
         lambda f: _at_least(f, 'losing_seasons_in_row', 2) and _no(f, 'coach_fired'),
         _slider('openness'), 0.25, 3,
         'After {losing_seasons_in_row} losing seasons in a row through {season} he is more willing to try new ideas.',
         ('losing_seasons_in_row', 'coach_fired')),
    Rule('COY_CONFIDENCE', frozenset({'coach'}),
         lambda f: COACH_OF_THE_YEAR in (f.get('awards') or ()),
         _slider('confidence'), 0.40, 2,
         'Since his {season} Coach of the Year award he trusts his own judgment more.',
         ('awards',)),
)

RULES_BY_ID = {r.rule_id: r for r in RULES}


def note_context(facts):
    """The values a note template may name: every fact, plus `award` (the first star award, readable)."""
    ctx = {k: v for k, v in facts.items() if not isinstance(v, (dict, list, set, tuple))}
    stars = _award(facts, PLAYER_STAR_AWARDS)
    ctx['award'] = AWARD_LABELS.get(stars[0], stars[0]) if stars else ''
    return ctx


def fill_note(rule, facts):
    return rule.note.format(**note_context(facts))
