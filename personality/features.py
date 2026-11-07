"""Personality feature switchboard (docs/done/PERSONALITY_ACTIVATION_PLAN.md §2.1-§2.2, §2.5; D1-D4).

One switchboard per franchise (keyed by league_state.madden_league_id): each personality feature's mode
(off | shadow | active), version, config and JR's review note, the history of every change, and shadow seasons
imported from tuning franchises as clearances. Tables from db/migrations/2027_01_31_personality_feature_modes.sql.
Nothing here switches a feature on by itself: a missing row (or a database without the tables) reads 'off'.

  mode(cur, league_id, feature) -> 'off' | 'shadow' | 'active'          missing row -> 'off'
  config(cur, league_id, feature) -> {mode, version, config, review_ref, review_note, shadow_since}
  set_mode(cur, league_id, feature, mode, *, version, config, review_ref, now, force_reason=None, set_by='JR')
  snapshot(cur, league_id) -> {league_id, source, features: {feature: config}}   frozen once per ingest / write
  check(cur, league_id, feature, mode, *, version, now) -> refusal reason | None   (no write)
  next_step(cur, league_id, feature, *, now) -> {next, allowed, why}
  history(cur, league_id, feature=None) -> [change]
  export_clearance(cur, league_id, feature, *, source_label, now, report_path=None) -> clearance document
  import_clearance(cur, league_id, document, *, cleared_by='JR') -> clearance row
  activate_cleared(cur, league_id, *, now, review_ref, set_by='JR') -> [result]

`league_id` is the league's integer id; a lifecycle universe id ('mu:<uuid>') is accepted too and resolved through
persona_lifecycle_universes (the contract store's read_contract_activation passes one). `now` is the franchise time
{season, tick} (league_state: current_season, current_week as the 32-tick relationships calendar).

Rules (set_mode; each refusal says why):
  D2  going active needs every earlier feature in FEATURES active ('profiles' also counts once a lifecycle
      inventory batch is applied for the league's universe);
  D3  going active needs a full simulated season in shadow on this franchise (shadow_since at or before tick 24 of
      the previous season, for the same version) or an imported clearance for this feature and version (D4);
  shadow (and active) need the feature's code installed: its module importable and its migration file present;
  force_reason skips D2 and D3 and is recorded; going off is always allowed; every change writes history.
'producers' and 'relationships' only ever mean "observe" in shadow: consumers stay neutral until they are active.

The caller owns the transaction: nothing here connects, commits or rolls back (the CLI does). Every statement is
tagged '/* q:<name> */' for the tests' fake cursor.

CLI: python -m personality.features show|set|history|export-clearance|import-clearance|activate-cleared (see
--help). Reads DATABASE_URL or --dsn, refuses madden_2036 and postgres. Exit codes: 0 ok, 2 bad request,
4 refused by a rule.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

FEATURES = ('producers', 'relationships', 'profiles', 'evolution', 'focus_refresh', 'contracts_ambition',
            'contracts_relationships', 'development_learning', 'trade_requests', 'retirement',
            'development_opportunity', 'coach_loop', 'player_loop', 'owner_patience', 'owner_hiring',
            'owner_spending', 'owner_meddling', 'scout_directors', 'media_voters')     # D2 go-live order
MODES = ('off', 'shadow', 'active')
OBSERVE_ONLY = ('producers', 'relationships')
TABLES = ('personality_feature_modes', 'personality_feature_mode_changes', 'personality_feature_clearances')
SHADOW_SEASON_TICK = 24          # OFFSEASON_START: a shadow from here last season covers one full simulated season
TICKS_PER_SEASON = 32
CLEARANCE_SCHEMA = 'personality-feature-clearance-v1'
REFUSED_DATABASES = ('madden_2036', 'postgres')
EXIT_OK, EXIT_INVALID, EXIT_REFUSED = 0, 2, 4
MIGRATIONS = Path(__file__).resolve().parent.parent / 'db' / 'migrations'

# feature -> (module that must import, migration file that must be present or None). The owner, scout and voter
# features need the owners plan's U6 migration (held), so they can't reach shadow until it is written.
REQUIREMENTS = {
    'producers': ('engines.relationship_producers.run', '2027_01_28_relationship_producers.sql'),
    'relationships': ('personality.relationships', '2027_01_27_personality_relationships.sql'),
    'profiles': ('personality.lifecycle', '2027_01_24_personality_lifecycle.sql'),
    'evolution': ('personality.evolution_run', '2027_01_30_personality_evolution.sql'),
    'focus_refresh': ('personality.focus_run', '2027_02_03_persona_focus.sql'),
    'contracts_ambition': ('engines.personality_contract_policy', '2027_01_23_contract_personality_policy.sql'),
    'contracts_relationships': ('engines.personality_contract_policy', '2027_01_23_contract_personality_policy.sql'),
    'development_learning': ('engines.personality_development', None),
    'trade_requests': ('engines.player_trade_requests', '2027_01_25_player_trade_requests.sql'),
    'retirement': ('engines.retirement_policy', '2027_01_26_retirement_policy.sql'),
    'development_opportunity': ('engines.personality_coaching', None),
    'coach_loop': ('engines.agent_loops.base', '2027_02_01_agent_loops.sql'),
    'player_loop': ('engines.agent_loops.base', '2027_02_01_agent_loops.sql'),
    'owner_patience': ('engines.owner_policy', '2027_02_02_owners_scouts_voters.sql'),
    'owner_hiring': ('engines.owner_policy', '2027_02_02_owners_scouts_voters.sql'),
    'owner_spending': ('engines.owner_policy', '2027_02_02_owners_scouts_voters.sql'),
    'owner_meddling': ('engines.owner_policy', '2027_02_02_owners_scouts_voters.sql'),
    'scout_directors': ('engines.scout_directors', '2027_02_02_owners_scouts_voters.sql'),
    'media_voters': ('newsroom.award_ballots', '2027_02_02_owners_scouts_voters.sql'),
}


class FeatureError(Exception):
    """A bad request (exit 2) or, as FeatureRefused, a request a rule refuses (exit 4)."""
    code = EXIT_INVALID


class FeatureRefused(FeatureError):
    code = EXIT_REFUSED


# --------------------------------------------------------------------------------------------- validation ---
def _feature(feature):
    if feature not in FEATURES:
        raise FeatureError(f'unknown feature {feature!r} (one of: {", ".join(FEATURES)})')
    return feature


def _mode(mode):
    if mode not in MODES:
        raise FeatureError(f'mode must be one of {", ".join(MODES)}, not {mode!r}')
    return mode


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise FeatureError(f'{label} must be non-empty text')
    return value


def _now(now):
    if not isinstance(now, dict):
        raise FeatureError('now must be {season, tick}')
    season, tick = now.get('season'), now.get('tick')
    for v, label in ((season, 'season'), (tick, 'tick')):
        if isinstance(v, bool) or not isinstance(v, int):
            raise FeatureError(f'now.{label} must be an integer')
    if not 1 <= tick <= TICKS_PER_SEASON:
        raise FeatureError(f'now.tick must be 1-{TICKS_PER_SEASON}')
    return {'season': season, 'tick': tick}


def _config(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FeatureError('config must be a JSON object')
    try:
        return json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as e:
        raise FeatureError(f'config must be JSON: {e}') from None


def _point(season, tick):
    return None if season is None else {'season': int(season), 'tick': int(tick)}


def _before_or_at(a, b):
    return (a['season'], a['tick']) <= (b['season'], b['tick'])


def full_shadow_season(since, now):
    """True when a shadow that began at `since` has covered one full simulated season by `now` (D3)."""
    return since is not None and _before_or_at(since, {'season': now['season'] - 1, 'tick': SHADOW_SEASON_TICK})


def _jsonb(value):
    try:
        from psycopg.types.json import Jsonb
        return Jsonb(value)
    except ImportError:          # tests and tools without psycopg
        return json.dumps(value, sort_keys=True)


def _loads(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


# ---------------------------------------------------------------------------------------------- storage ---
def tables_ready(cur):
    cur.execute('/* q:ready */ SELECT to_regclass(%s) IS NOT NULL, to_regclass(%s) IS NOT NULL, '
                'to_regclass(%s) IS NOT NULL', TABLES)
    row = cur.fetchone()
    return bool(row) and all(row)


def league_key(cur, key):
    """The integer league id for `key` (an int, a numeric string or a lifecycle universe id), or None."""
    if isinstance(key, bool):
        raise FeatureError('league_id must be an integer or a universe id')
    if isinstance(key, int):
        return key
    if isinstance(key, str) and re.fullmatch(r'-?\d+', key.strip()):
        return int(key)
    if isinstance(key, str) and key.startswith('mu:'):
        cur.execute('/* q:universe_ready */ SELECT to_regclass(%s) IS NOT NULL', ('persona_lifecycle_universes',))
        if not cur.fetchone()[0]:
            return None
        cur.execute('/* q:universe_league */ SELECT league_id FROM persona_lifecycle_universes WHERE universe_id = %s',
                    (key,))
        row = cur.fetchone()
        return None if row is None or row[0] is None else int(row[0])
    raise FeatureError('league_id must be an integer or a universe id')


def _off(feature):
    return {'feature': feature, 'mode': 'off', 'version': None, 'config': {}, 'review_ref': None,
            'review_note': None, 'shadow_since': None, 'set_by': None}


def _row(r):
    feature, mode, version, cfg, review, s_season, s_tick, set_by = r
    return {'feature': feature, 'mode': mode, 'version': version, 'config': _loads(cfg), 'review_ref': review,
            'review_note': review, 'shadow_since': _point(s_season, s_tick), 'set_by': set_by}


def _read_rows(cur, league):
    cur.execute('/* q:modes */ SELECT feature, mode, version, config, review_ref, shadow_since_season, '
                'shadow_since_tick, set_by FROM personality_feature_modes WHERE league_id = %s', (league,))
    return {r[0]: _row(r) for r in cur.fetchall()}


def config(cur, league_id, feature):
    """{feature, mode, version, config, review_ref, review_note, shadow_since, set_by}; 'off' when there is no row,
    no tables or no league for the key."""
    _feature(feature)
    league = league_key(cur, league_id)
    if league is None or not tables_ready(cur):
        return _off(feature)
    return _read_rows(cur, league).get(feature) or _off(feature)


def mode(cur, league_id, feature):
    return config(cur, league_id, feature)['mode']


def snapshot(cur, league_id):
    """Every feature's config at once, frozen for one ingest or league-office write. Stable for unchanged state."""
    league = league_key(cur, league_id)
    if league is None or not tables_ready(cur):
        return {'league_id': league, 'source': 'default', 'features': {f: _off(f) for f in FEATURES}}
    rows = _read_rows(cur, league)
    return {'league_id': league, 'source': 'switchboard',
            'features': {f: rows.get(f) or _off(f) for f in FEATURES}}


def snapshot_mode(snap, feature):
    """A feature's mode from a snapshot (None or a missing feature reads 'off')."""
    _feature(feature)
    return (((snap or {}).get('features') or {}).get(feature) or {}).get('mode') or 'off'


def _clearance(cur, league, feature, version=None):
    if version is None:
        cur.execute('/* q:clearance_latest */ SELECT feature, version, source_db, source_label, shadow_from_season, '
                    'shadow_from_tick, shadow_to_season, shadow_to_tick, report_path, report_sha256, config, '
                    'cleared_by FROM personality_feature_clearances WHERE league_id = %s AND feature = %s '
                    'ORDER BY cleared_at DESC, version DESC LIMIT 1', (league, feature))
    else:
        cur.execute('/* q:clearance */ SELECT feature, version, source_db, source_label, shadow_from_season, '
                    'shadow_from_tick, shadow_to_season, shadow_to_tick, report_path, report_sha256, config, '
                    'cleared_by FROM personality_feature_clearances WHERE league_id = %s AND feature = %s '
                    'AND version = %s', (league, feature, version))
    r = cur.fetchone()
    if r is None:
        return None
    return {'feature': r[0], 'version': r[1], 'source_db': r[2], 'source_label': r[3],
            'shadow_from': _point(r[4], r[5]), 'shadow_to': _point(r[6], r[7]), 'report_path': r[8],
            'report_sha256': r[9], 'config': _loads(r[10]), 'cleared_by': r[11]}


def profiles_batch_applied(cur, league):
    """True when a lifecycle inventory batch is applied for the league's universe (counts as 'profiles' active)."""
    cur.execute('/* q:lifecycle_ready */ SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL',
                ('persona_lifecycle_universes', 'persona_lifecycle_batches'))
    if not cur.fetchone()[0]:
        return False
    cur.execute("/* q:lifecycle_batch */ SELECT EXISTS (SELECT 1 FROM persona_lifecycle_batches b "
                "JOIN persona_lifecycle_universes u ON u.universe_id = b.universe_id "
                "WHERE u.league_id = %s AND b.status = 'applied')", (str(league),))
    return bool(cur.fetchone()[0])


def installed(feature):
    """(True, None) when the feature's code is installed, else (False, why)."""
    module, migration = REQUIREMENTS[_feature(feature)]
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        return False, f'{feature} code is not installed (module {module} not found)'
    if migration and not (MIGRATIONS / migration).exists():
        return False, f'{feature} code is not installed (migration {migration} not present)'
    return True, None


# ------------------------------------------------------------------------------------------------- rules ---
def _is_active(cur, league, rows, feature):
    if (rows.get(feature) or {}).get('mode') == 'active':
        return True
    return feature == 'profiles' and profiles_batch_applied(cur, league)


def _refusal(cur, league, rows, feature, target, version, now, force_reason):
    """The reason a change is refused, or None."""
    if target == 'off':
        return None
    ok, why = installed(feature)
    if not ok:
        return why
    if target == 'shadow' or force_reason:
        return None
    for earlier in FEATURES[:FEATURES.index(feature)]:
        if not _is_active(cur, league, rows, earlier):
            return f'D2: {earlier} must be active before {feature} can go active (go-live order)'
    row = rows.get(feature) or {}
    if (row.get('mode') in ('shadow', 'active') and row.get('version') == version
            and full_shadow_season(row.get('shadow_since'), now)):
        return None
    if _clearance(cur, league, feature, version) is not None:
        return None
    since = row.get('shadow_since') if row.get('version') == version else None
    where = (f'shadow since {since["season"]} tick {since["tick"]}' if since else f'no shadow of version {version}')
    return (f'D3: {feature} {version} needs one full simulated season in shadow (shadow since tick '
            f'{SHADOW_SEASON_TICK} of {now["season"] - 1} or earlier; {where}) or an imported clearance for that '
            'version')


def _require_tables(cur):
    if not tables_ready(cur):
        raise FeatureRefused('the switchboard tables are not migrated (db/migrations/'
                             '2027_01_31_personality_feature_modes.sql)')


def _league(cur, league_id):
    league = league_key(cur, league_id)
    if league is None:
        raise FeatureError(f'no league for {league_id!r}')
    return league


def check(cur, league_id, feature, target, *, version, now, force_reason=None):
    """The refusal set_mode would give, or None (writes nothing)."""
    _feature(feature), _mode(target)
    now = _now(now)
    league = _league(cur, league_id)
    if not tables_ready(cur):
        return None if target == 'off' else 'the switchboard tables are not migrated'
    return _refusal(cur, league, _read_rows(cur, league), feature, target, version, now, force_reason)


def set_mode(cur, league_id, feature, mode, *, version, config=None, review_ref=None, now, force_reason=None,
             set_by='JR'):
    """Change one feature's mode under D2/D3 (module docstring). Returns the change receipt. config None keeps the
    current config (or, going active on a clearance, the clearance's config)."""
    _feature(feature), _mode(mode)
    _text(version, 'version')
    _text(set_by, 'set_by')
    now = _now(now)
    if force_reason is not None:
        _text(force_reason, 'force_reason')
    league = _league(cur, league_id)
    _require_tables(cur)
    rows = _read_rows(cur, league)
    why = _refusal(cur, league, rows, feature, mode, version, now, force_reason)
    if why:
        raise FeatureRefused(why)
    current = rows.get(feature) or _off(feature)
    if config is None:
        cfg = deepcopy(current['config']) if current['version'] == version else {}
        if mode == 'active' and not cfg:
            cl = _clearance(cur, league, feature, version)
            cfg = deepcopy(cl['config']) if cl else {}
    else:
        cfg = _config(config)
    since = current['shadow_since'] if current['version'] == version else None
    if mode == 'shadow' and not (current['mode'] == 'shadow' and since is not None):
        since = dict(now)                         # a new shadow (or a new version) starts its season now
    if mode == 'off':
        since = None
    cur.execute('/* q:upsert */ INSERT INTO personality_feature_modes (league_id, feature, mode, version, config, '
                'review_ref, shadow_since_season, shadow_since_tick, set_by, set_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (league_id, feature) DO UPDATE SET '
                'mode = EXCLUDED.mode, version = EXCLUDED.version, config = EXCLUDED.config, '
                'review_ref = EXCLUDED.review_ref, shadow_since_season = EXCLUDED.shadow_since_season, '
                'shadow_since_tick = EXCLUDED.shadow_since_tick, set_by = EXCLUDED.set_by, set_at = now()',
                (league, feature, mode, version, _jsonb(cfg), review_ref,
                 since and since['season'], since and since['tick'], set_by))
    cur.execute('/* q:change */ INSERT INTO personality_feature_mode_changes (league_id, feature, from_mode, to_mode, '
                'version, review_ref, force_reason, at_season, at_tick, set_by) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (league, feature, None if feature not in rows else current['mode'], mode, version, review_ref,
                 force_reason, now['season'], now['tick'], set_by))
    return {'league_id': league, 'feature': feature, 'from_mode': current['mode'], 'to_mode': mode,
            'version': version, 'config': cfg, 'review_ref': review_ref, 'force_reason': force_reason,
            'shadow_since': since, 'at': now, 'set_by': set_by}


def next_step(cur, league_id, feature, *, now):
    """{next, allowed, why}: the feature's next mode (off -> shadow -> active) and whether a rule allows it now."""
    row = config(cur, league_id, feature)
    if row['mode'] == 'active':
        return {'next': None, 'allowed': False, 'why': 'already active'}
    target = 'shadow' if row['mode'] == 'off' else 'active'
    version = row['version'] or '<version>'
    why = check(cur, league_id, feature, target, version=version, now=now)
    return {'next': target, 'allowed': why is None, 'why': why or 'allowed'}


def history(cur, league_id, feature=None):
    league = _league(cur, league_id)
    if not tables_ready(cur):
        return []
    if feature is not None:
        _feature(feature)
    cur.execute('/* q:history */ SELECT id, feature, from_mode, to_mode, version, review_ref, force_reason, '
                'at_season, at_tick, set_by, created_at FROM personality_feature_mode_changes WHERE league_id = %s '
                'AND (%s::text IS NULL OR feature = %s) ORDER BY id', (league, feature, feature))
    keys = ('id', 'feature', 'from_mode', 'to_mode', 'version', 'review_ref', 'force_reason', 'at_season',
            'at_tick', 'set_by', 'created_at')
    return [dict(zip(keys, r)) for r in cur.fetchall()]


# -------------------------------------------------------------------------------------------- clearances ---
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def database_of(cur):
    cur.execute('/* q:dbname */ SELECT current_database()')
    row = cur.fetchone()
    return row[0] if row else None


def export_clearance(cur, league_id, feature, *, source_label, now, report_path=None):
    """On a tuning franchise: the clearance document for the feature's current version. Needs one full simulated
    season in shadow (D3) and a readable shadow report (its path from the argument or config['report_path'])."""
    _feature(feature)
    _text(source_label, 'source_label')
    now = _now(now)
    league = _league(cur, league_id)
    _require_tables(cur)
    row = _read_rows(cur, league).get(feature)
    if row is None or row['mode'] not in ('shadow', 'active'):
        raise FeatureRefused(f'{feature} is not in shadow on this franchise: nothing to clear')
    if not full_shadow_season(row['shadow_since'], now):
        since = row['shadow_since']
        raise FeatureRefused(f'D3: {feature} {row["version"]} has not had a full simulated season in shadow '
                             f'(shadow since {since and (since["season"], since["tick"])}, now '
                             f'{now["season"]} tick {now["tick"]})')
    path = report_path or row['config'].get('report_path')
    if not path:
        raise FeatureError(f'{feature}: no shadow report (pass --report or set config.report_path)')
    if not os.path.isfile(path):
        raise FeatureRefused(f'{feature}: shadow report {path} not found')
    return {'schema': CLEARANCE_SCHEMA, 'feature': feature, 'version': row['version'],
            'source_db': database_of(cur) or 'unknown', 'source_label': source_label, 'source_league_id': league,
            'shadow_from': row['shadow_since'], 'shadow_to': now, 'report_path': os.path.abspath(path),
            'report_sha256': file_sha256(path), 'config': row['config']}


def _validate_document(doc):
    if not isinstance(doc, dict) or doc.get('schema') != CLEARANCE_SCHEMA:
        raise FeatureError(f'not a clearance document (schema {CLEARANCE_SCHEMA})')
    _feature(doc.get('feature'))
    for key in ('version', 'source_db', 'source_label', 'report_path', 'report_sha256'):
        _text(doc.get(key), key)
    frm, to = _now(doc.get('shadow_from') or {}), _now(doc.get('shadow_to') or {})
    if not full_shadow_season(frm, to):
        raise FeatureRefused('D3: the clearance does not cover one full simulated season in shadow')
    _config(doc.get('config'))
    return frm, to


def import_clearance(cur, league_id, document, *, cleared_by='JR'):
    """On the real franchise: store a tuning franchise's clearance after checking feature, version, the full
    shadow season and the report hash (the report must still read the same). Re-importing the same clearance is a
    replay; a different one for the same feature and version is refused."""
    frm, to = _validate_document(document)
    _text(cleared_by, 'cleared_by')
    league = _league(cur, league_id)
    _require_tables(cur)
    path = document['report_path']
    if not os.path.isfile(path):
        raise FeatureRefused(f'shadow report {path} not found: the clearance cannot be checked')
    if file_sha256(path) != document['report_sha256']:
        raise FeatureRefused(f'shadow report {path} does not match the clearance hash (changed or tampered)')
    feature, version = document['feature'], document['version']
    have = _clearance(cur, league, feature, version)
    cfg = _config(document.get('config'))
    if have is not None:
        same = (have['report_sha256'] == document['report_sha256'] and have['config'] == cfg
                and have['shadow_from'] == frm and have['shadow_to'] == to)
        if not same:
            raise FeatureRefused(f'{feature} {version} already has a different clearance')
        return dict(have, status='replayed')
    cur.execute('/* q:clearance_insert */ INSERT INTO personality_feature_clearances (league_id, feature, version, '
                'source_db, source_label, shadow_from_season, shadow_from_tick, shadow_to_season, shadow_to_tick, '
                'report_path, report_sha256, config, cleared_by) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (league, feature, version, document['source_db'], document['source_label'], frm['season'],
                 frm['tick'], to['season'], to['tick'], path, document['report_sha256'], _jsonb(cfg), cleared_by))
    return {'feature': feature, 'version': version, 'source_db': document['source_db'],
            'source_label': document['source_label'], 'shadow_from': frm, 'shadow_to': to, 'report_path': path,
            'report_sha256': document['report_sha256'], 'config': cfg, 'cleared_by': cleared_by,
            'status': 'imported'}


def activate_cleared(cur, league_id, *, now, review_ref, set_by='JR'):
    """On the real franchise: set every cleared feature active in FEATURES order with its clearance's version and
    config; stops at the first feature that is neither active nor cleared. Returns one result per feature reached."""
    now = _now(now)
    _text(review_ref, 'review_ref')
    league = _league(cur, league_id)
    _require_tables(cur)
    out = []
    for feature in FEATURES:
        rows = _read_rows(cur, league)
        if _is_active(cur, league, rows, feature):
            out.append({'feature': feature, 'status': 'already_active'})
            continue
        cl = _clearance(cur, league, feature)
        if cl is None:
            out.append({'feature': feature, 'status': 'stopped', 'reason': 'no clearance imported'})
            break
        try:
            receipt = set_mode(cur, league, feature, 'active', version=cl['version'], config=cl['config'],
                               review_ref=review_ref, now=now, set_by=set_by)
        except FeatureRefused as e:
            out.append({'feature': feature, 'status': 'stopped', 'reason': str(e)})
            break
        out.append({'feature': feature, 'status': 'activated', 'version': cl['version'], 'receipt': receipt})
    return out


# --------------------------------------------------------------------------------------------------- CLI ---
def connect(dsn):
    """A non-autocommit connection. Tests replace this with a fake."""
    import psycopg
    return psycopg.connect(dsn)


def database_name(dsn):
    try:
        from psycopg.conninfo import conninfo_to_dict
        return conninfo_to_dict(dsn).get('dbname')
    except Exception:  # noqa: BLE001 - an unparsable DSN is refused below
        m = re.search(r'/([^/?]+)(\?|$)', dsn or '')
        return m.group(1) if m else None


def _dsn(args):
    dsn = args.dsn or os.environ.get('DATABASE_URL')
    if not dsn:
        raise FeatureError('no database: pass --dsn or set DATABASE_URL')
    name = database_name(dsn)
    if name is None or name in REFUSED_DATABASES:
        raise FeatureError(f'refusing database {name!r} (madden_2036 and postgres are refused)')
    return dsn


def league_now(cur):
    """(league_id, {season, tick}) from league_state (current_week as a 1-32 tick)."""
    cur.execute('/* q:league_state */ SELECT madden_league_id, current_season, current_week FROM league_state '
                'WHERE id = 1')
    row = cur.fetchone()
    if not row or row[0] is None:
        raise FeatureError('league_state.madden_league_id is not set (load a save first)')
    return int(row[0]), {'season': int(row[1]), 'tick': max(1, min(TICKS_PER_SEASON, int(row[2] or 1)))}


def _show(cur, league, now):
    out = []
    for feature in FEATURES:
        row = config(cur, league, feature)
        step = next_step(cur, league, feature, now=now)
        out.append({'feature': feature, 'mode': row['mode'], 'version': row['version'],
                    'shadow_since': row['shadow_since'], 'review_ref': row['review_ref'],
                    'report': (row['config'] or {}).get('report_path'), 'next': step['next'],
                    'allowed': step['allowed'], 'why': step['why']})
    return out


def parser():
    ap = argparse.ArgumentParser(prog='python -m personality.features',
                                 description='Personality feature switchboard (PERSONALITY_ACTIVATION_PLAN.md).')
    sub = ap.add_subparsers(dest='command', required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument('--dsn', help='database (default DATABASE_URL; madden_2036 and postgres are refused)')
        return p

    add('show', "each feature's mode, version, shadow start and next allowed step")
    p = add('set', "change one feature's mode (D2 order, D3 shadow season)")
    p.add_argument('feature', choices=FEATURES)
    p.add_argument('mode', choices=MODES)
    p.add_argument('--version', required=True)
    p.add_argument('--review', required=True, help="JR's review note")
    p.add_argument('--config', help='JSON file with the feature config')
    p.add_argument('--force', metavar='REASON', help='skip the order and shadow-season rules (recorded)')
    p = add('history', 'every mode change')
    p.add_argument('--feature', choices=FEATURES)
    p = add('export-clearance', "write a tuning franchise's shadow-season clearance")
    p.add_argument('feature', choices=FEATURES)
    p.add_argument('--label', required=True, help='the tuning save, e.g. CAREER-TUNE-2026a')
    p.add_argument('--out', required=True)
    p.add_argument('--report', help="the feature's shadow report (default config.report_path)")
    p = add('import-clearance', 'import a clearance into this franchise')
    p.add_argument('path')
    p = add('activate-cleared', 'switch on every cleared feature in order (real franchise, before 2026 week 1)')
    p.add_argument('--review', required=True)
    return ap


def run_cli(args, out=print):
    dsn = _dsn(args)
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        league, now = league_now(cur)
        if args.command == 'show':
            result = {'league_id': league, 'now': now, 'features': _show(cur, league, now)}
        elif args.command == 'history':
            result = history(cur, league, args.feature)
        elif args.command == 'set':
            cfg = None
            if args.config:
                try:
                    cfg = json.loads(Path(args.config).read_text())
                except (OSError, ValueError) as e:
                    raise FeatureError(f'--config: {e}') from None
            result = set_mode(cur, league, args.feature, args.mode, version=args.version, config=cfg,
                              review_ref=args.review, now=now, force_reason=args.force)
        elif args.command == 'export-clearance':
            result = export_clearance(cur, league, args.feature, source_label=args.label, now=now,
                                      report_path=args.report)
            Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True, default=str) + '\n')
        elif args.command == 'import-clearance':
            try:
                doc = json.loads(Path(args.path).read_text())
            except (OSError, ValueError) as e:
                raise FeatureError(f'{args.path}: {e}') from None
            result = import_clearance(cur, league, doc)
        else:
            result = activate_cleared(cur, league, now=now, review_ref=args.review)
        if args.command in ('set', 'import-clearance', 'activate-cleared'):
            conn.commit()
        else:
            conn.rollback()
        out(json.dumps(result, indent=1, sort_keys=True, default=str))
        return EXIT_OK
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None, out=print, err=None):
    err = err or (lambda m: print(m, file=sys.stderr))
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:
        return EXIT_INVALID if e.code not in (0, None) else EXIT_OK
    try:
        return run_cli(args, out=out)
    except FeatureError as e:
        err(f'{"refused" if e.code == EXIT_REFUSED else "error"}: {e}')
        return e.code


if __name__ == '__main__':
    sys.exit(main())
