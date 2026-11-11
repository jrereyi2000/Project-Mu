"""No-model V3 evidence probe. Explicit DSN; all connections forced read-only."""
import hashlib
import json
import os
from pathlib import Path


def main():
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('Set DATABASE_URL explicitly')
    import psycopg
    from psycopg.conninfo import make_conninfo
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on'
    os.environ['DATABASE_URL'] = make_conninfo(os.environ['DATABASE_URL'], options='-c default_transaction_read_only=on')
    from newsroom.run_context import article_run_context
    from newsroom import db_tools
    from tools.dossier_ab_compare import compile_matchup_dossier
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
    dsn = os.environ['DATABASE_URL']
    dispatcher = SwarmDispatcher(debug_mode=False)
    output = Path('verify_output/v3_dossier_offline')
    output.mkdir(parents=True, exist_ok=True)
    summary = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_setting('transaction_read_only')")
        database, read_only = cur.fetchone()
        assert read_only == 'on'
        for week, opponent in [(2, 'PIT'), (12, 'MIN'), (21, 'AZ')]:
            cur.execute("SELECT id, name FROM teams WHERE abbreviation='CAR'")
            team_id, name = cur.fetchone()
            cur.execute('SELECT id, name FROM teams WHERE abbreviation=%s', (opponent,))
            opponent_id, opponent_name = cur.fetchone()
            cur.execute('SELECT id FROM games WHERE season=2036 AND week=%s AND ((home_team_id=%s AND away_team_id=%s) OR (home_team_id=%s AND away_team_id=%s))', (week, team_id, opponent_id, opponent_id, team_id))
            game_id = cur.fetchone()[0]
            cur.execute("SELECT agent_id, archetype, persistent_memory, handle, display_name FROM agent_memories WHERE assigned_team_id=%s AND archetype='LOCAL_BEAT_REPORTER' ORDER BY agent_id LIMIT 1", (team_id,))
            agent_id, archetype, memory, handle, display = cur.fetchone()
            unrestricted = db_tools.get_team_efficiency_profile('CAR', season=2036)
            with article_run_context(2036, week):
                brief = build_weekly_assignment_brief(dsn, 2036, week)
                prompt = dispatcher.compile_agent_prompt(agent_id=agent_id, archetype=archetype, persistent_memory=memory, handle=handle, display_name=display, assigned_team_id=team_id, brief=brief, season=2036, week=week, content_type='ARTICLE', article_type='LOCAL_MATCHUP_PRIMER', slot_target={'team_id': team_id, 'team_name': name, 'opponent_team_id': opponent_id, 'opponent_name': opponent_name, 'game_id': game_id, 'note': f'Matchup preview: CAR vs {opponent}'})
                dossier = compile_matchup_dossier('CAR', opponent, 2036, week)
                restricted = db_tools.get_team_efficiency_profile('CAR', season=2036)
            packet = dict(schema_version=1, database=database, read_only=read_only, season=2036, publication_week=week, game_id=game_id, writer=agent_id, baseline_prompt=prompt, dossier=dossier, cutoff_profile=restricted, unrestricted_profile=unrestricted, cutoff_changes_profile=restricted != unrestricted, tools=[t.name for t in dispatcher.tools_for('LOCAL_MATCHUP_PRIMER')], limitations=['Latest-only aggregates/ratings/rosters are not historical snapshots', 'Research directives remain: not a production-ready tool-free prompt', 'No model outputs or factual-quality verdict'])
            encoded = json.dumps(packet, sort_keys=True, indent=2, default=str).encode()
            path = output / f'2036_w{week}_CAR_{opponent}.json'
            path.write_bytes(encoded)
            summary.append(dict(file=str(path), sha256=hashlib.sha256(encoded).hexdigest(), game_id=game_id, writer=agent_id, cutoff_changes_profile=restricted != unrestricted, dossier_chars=len(dossier), tool_count=len(packet['tools'])))
    (output / 'manifest.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
