"""Development evolution candidates: the development/coaching plan's season outputs as external candidates for the
evolution arbiter (docs/done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md §2.6, docs/done/PERSONALITY_EVOLUTION_PLAN.md
§2.3 S3: source 'DEVELOPMENT', in the relationships plan's review-result shape).

  development_mode(evolution_mode, development_mode) -> 'enabled' | 'shadow' | None    which receipts may be read
  season_receipts(cur, season) -> [receipt]          APPLIED progression receipts of the earned season that carry
                                                     the personality_development_v1 block
  candidates(receipts, people, *, universe_id, season, receipt_mode) -> [external item]      pure
  external_candidates(cur, universe_id, season, *, receipt_mode) -> [external item]          reads, then candidates

Development's season output is its private attribution on progression receipts (progression_events.metadata
'personality_development_v1'). One good week can't change a profile (plan §2.6), so a candidate needs sustained
evidence: at least MIN_CHECKPOINTS separate receipts in the earned season on which the player's own preparation
channels (drive, film, technical preparation, coasting) were net positive and an effect resulted.

Rule (development-evolution-v1, review-only like the evolution rules' chances):
  SUSTAINED_PREPARATION  a player who carries COASTING, with sustained net-positive preparation -> remove COASTING.

Each item is {source 'DEVELOPMENT', persona_type, persona_id, universe_id, status 'ready', priority, candidate:
{event_id, source_event_id, season, rule_version, reason, kind, remove, supporting_source_ids, review_status
'approved', reviewer, feature_scope}}. The rule set is the review (like the evolution rules), so items are 'ready';
the arbiter's own core check (revision, annual slot, validity) stays final and a person still gets one change a
season. PRIORITY (3) puts them among the evolution rules instead of EXTERNAL_PRIORITY 0. The source IDs are the
progression receipts' event_ids.

Gate (the watcher's evolution step): nothing is read unless the switchboard's `development_learning` feature and
the evolution step are both on. Development active -> its 'enabled' receipts (effects really applied); development
shadow -> its 'shadow' receipts, but only while evolution is a dry run too, so a shadow effect never changes a
profile. Everything stays off by default.
"""
from __future__ import annotations

from engines.personality_development import COASTING_TRAIT, PLAYER_CHANNELS, POLICY_SCHEMA

RULE_VERSION = 'development-evolution-v1'
MIN_CHECKPOINTS = 3
PRIORITY = 3
FEATURE = 'development_learning'
RULE_ID = 'SUSTAINED_PREPARATION'
NOTE = 'After a {season} season of steady preparation he no longer coasts.'


def development_mode(evolution_mode, dev_mode):
    """Which receipts feed evolution: 'enabled' (development active), 'shadow' (development shadow while evolution
    is a dry run), or None (either feature off, or development shadow against an applying evolution)."""
    if evolution_mode not in ('shadow', 'active') or dev_mode not in ('shadow', 'active'):
        return None
    if dev_mode == 'active':
        return 'enabled'
    return 'shadow' if evolution_mode == 'shadow' else None


def _net_preparation(block):
    total = 0.0
    for ratings in (block.get('raw_channels') or {}).values():
        for channels in (ratings or {}).values():
            total += sum(float((channels or {}).get(c) or 0.0) for c in PLAYER_CHANNELS)
    return total


def _effect(block, receipt_mode):
    key = 'attributed_effect' if receipt_mode == 'enabled' else 'shadow_effect'
    return sum(float(v) for v in (block.get(key) or {}).values())


def qualifying(receipts, receipt_mode):
    """{player_id: [receipt event_id]} of receipts (in the given mode) with net-positive preparation and an effect."""
    out = {}
    for r in sorted(receipts, key=lambda r: str(r['event_id'])):
        block = (r.get('metadata') or {}).get(POLICY_SCHEMA)
        if not block or block.get('mode') != receipt_mode:
            continue
        if _net_preparation(block) > 0 and _effect(block, receipt_mode) > 0:
            out.setdefault(r['player_id'], []).append(str(r['event_id']))
    return out


def candidates(receipts, people, *, universe_id, season, receipt_mode):
    """External candidates (see the module docstring). `people` is {persona_id: {role, profile_view, ids:
    {'db-player': [players.id]}}} (personality.evolution_facts' people map). Pure; inputs are not mutated."""
    if receipt_mode not in ('enabled', 'shadow'):
        return []
    by_player = qualifying(receipts, receipt_mode)
    out = []
    for pid in sorted(people, key=str):
        person = people[pid]
        if person.get('role') != 'player':
            continue
        if COASTING_TRAIT not in set((person.get('profile_view') or {}).get('traits') or ()):
            continue
        sids = sorted({s for p in (person.get('ids') or {}).get('db-player') or () for s in by_player.get(p, ())})
        if len(sids) < MIN_CHECKPOINTS:
            continue
        out.append({'source': 'DEVELOPMENT', 'persona_type': 'player', 'persona_id': pid,
                    'universe_id': universe_id, 'status': 'ready', 'priority': PRIORITY,
                    'candidate': {'event_id': f'dev:{universe_id}:{pid}:{season}:{RULE_ID}',
                                  'source_event_id': sids[0], 'season': season, 'rule_version': RULE_VERSION,
                                  'reason': NOTE.format(season=season), 'kind': 'trait', 'remove': COASTING_TRAIT,
                                  'supporting_source_ids': sids, 'review_status': 'approved',
                                  'reviewer': f'rule:{RULE_VERSION}', 'feature_scope': [FEATURE]}})
    return out


def season_receipts(cur, season):
    """APPLIED progression receipts of the earned season carrying the development block ([] without the table)."""
    cur.execute("/* q:dev_ready */ SELECT to_regclass('progression_events') IS NOT NULL")
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    cur.execute('/* q:dev_receipts */ SELECT event_id, player_id, kind, metadata FROM progression_events '
                "WHERE earned_season = %s AND status = 'APPLIED' AND metadata ? %s ORDER BY event_id",
                (season, POLICY_SCHEMA))
    return [{'event_id': e, 'player_id': p, 'kind': k, 'metadata': m or {}} for e, p, k, m in cur.fetchall()]


def external_candidates(cur, universe_id, season, *, receipt_mode):
    """The season's development candidates for run_season(external=...); [] when receipt_mode is None."""
    if receipt_mode is None:
        return []
    from personality.evolution_facts import _people
    receipts = season_receipts(cur, season)
    if not receipts:
        return []
    return candidates(receipts, _people(cur, universe_id), universe_id=universe_id, season=season,
                      receipt_mode=receipt_mode)
