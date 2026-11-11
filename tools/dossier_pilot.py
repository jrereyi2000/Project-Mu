"""Isolated same-model matchup pilot; defaults to offline preparation only.

Use an explicit scratch DATABASE_URL whose name begins madden_v3_eval_.
No publication, memory updates, or database usage logging. Results are local files.
--provider gemini (default) keeps the matched Gemini ReAct/dossier arms;
--provider deepseek runs both arms on deepseek-flash through the OpenAI-compatible
tool loop, priced at DeepSeek's peak or off-peak rate by each call's UTC time.
"""
import argparse
import csv
import datetime as dt
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

POLICY = '''CANONICAL FACTS AND CREATIVE FREEDOM (apply equally to both variants):
Invent quotes, private reporting access, scenes, dialogue and unrecorded plays freely
where canonical data leaves room. Invention alone is not a factual error.
Never contradict recorded scores, scoring sequences, statistics, identities,
participation, injuries, transactions, awards, records or chronology. Invented plays
must fit the recorded game script and box score; invented quotes must not introduce
false canonical facts. Unknown canonical numbers remain unknown, not zero or invented.
Records and superlatives require the correct scope: distinguish tied from broken,
team from league leaders, and per-game statistics from rates or pressure splits.
Latest-only ratings/roster/aggregate fields describe the shared archive snapshot, not
proven historical weekly values. Do not present later totals as earlier-week facts.
Keep analysis and predictions distinguishable from canonical outcomes.
'''
PRICES = dict(input=0.75, cached=0.075, output=3.75)
PRICE_SOURCE = 'https://ai.google.dev/gemini-api/docs/pricing'
GEMINI_MODEL = 'gemini-3.8-flash:low'
DEEPSEEK_MODEL = 'deepseek-flash'
# DeepSeek list prices per 1M tokens, checked 2026-11-11 against the API docs'
# Models & Pricing table. Off-peak is 50% of peak.
DEEPSEEK_PEAK = dict(uncached=0.30, cached=0.006, output=1.20)
DEEPSEEK_OFF_PEAK = dict(uncached=0.15, cached=0.003, output=0.60)
DEEPSEEK_PRICE_SOURCE = 'https://api-docs.deepseek.com/quick_start/pricing'
DEEPSEEK_PRICE_CHECKED = '2026-11-11'
DEEPSEEK_INPUT_LIMIT = 1_000_000
TEMPERATURE = 0.7


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str))


def deepseek_rates(at=None):
    """Return (rate table, period label) for a call at ``at`` (UTC).

    Peak is Monday-Friday 01:00-04:00 and 06:00-10:00 UTC. DeepSeek also makes
    Chinese public holidays off-peak all day; no holiday calendar is kept here,
    so those weekdays are conservatively priced as peak.
    """
    when = at or dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    when = when.astimezone(dt.timezone.utc)
    peak = when.weekday() < 5 and (1 <= when.hour < 4 or 6 <= when.hour < 10)
    return (DEEPSEEK_PEAK if peak else DEEPSEEK_OFF_PEAK), ('peak' if peak else 'off-peak')


def cost(usage, provider='gemini', at=None, rates=None):
    cached = min(usage['cached_tokens'], usage['input_tokens'])
    if provider == 'deepseek':
        # DeepSeek's completion_tokens already include reasoning tokens, so
        # thinking is reported separately but billed once at the output rate.
        table = rates or deepseek_rates(at)[0]
        uncached = usage.get('uncached_tokens')
        if uncached is None:
            uncached = usage['input_tokens'] - cached
        return (uncached * table['uncached'] + cached * table['cached']
                + usage['output_tokens'] * table['output']) / 1e6
    return ((usage['input_tokens'] - cached) * PRICES['input'] + cached * PRICES['cached']
            + (usage['output_tokens'] + usage['thinking_tokens']) * PRICES['output']) / 1e6


def dossier_instructions(text):
    """Rewrite access instructions only; retain editorial/factual constraints."""
    text = re.sub(r'AVAILABLE DATABASE REAC/TOOLS:.*?(?=WEEK CONTEXT:)',
                  'EVIDENCE ACCESS: No tools are available. Consult the supplied evidence sections.\n\n',
                  text, flags=re.S)
    text = text.replace('You have access to autonomous database tools to investigate box scores, player stats, season standings, and team analytics for any game or franchise.',
                        'Use the supplied evidence to investigate your assigned subjects.')
    text = text.replace('If you need specific stats, use your database tools before producing your final text.',
                        'Use supplied evidence for canonical stats; unknown numbers remain unknown. Creative texture is allowed where it does not contradict data.')
    text = text.replace('2. RESEARCH TOOLS:', '2. EVIDENCE SOURCES:')
    text = re.sub(r'\b[Cc]all\s+(`get_\w+`)', r'Consult supplied evidence from \1', text)
    text = text.replace('- Query ', '- Consult supplied evidence for ')
    text = text.replace(' or query ', ' or consult supplied evidence from ')
    text = text.replace('your injury tool', 'the supplied injury evidence')
    text = re.sub(r'\b[Cc]all (get_\w+)', r'Consult supplied evidence from \1', text)
    return text


def configure_database():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    raw = os.environ.get('DATABASE_URL')
    if not raw or not conninfo_to_dict(raw).get('dbname', '').startswith('madden_v3_eval_'):
        raise ValueError('Explicit scratch DATABASE_URL (dbname=madden_v3_eval_*) required')
    os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on'
    dsn = make_conninfo(raw, options='-c default_transaction_read_only=on')
    os.environ['DATABASE_URL'] = dsn
    with psycopg.connect(dsn) as conn:
        database, ro = conn.execute("SELECT current_database(), current_setting('transaction_read_only')").fetchone()
        if ro != 'on':
            raise RuntimeError('Read-only enforcement failed')
    return dsn, database


def prepare(dsn, database, week, opponent):
    import psycopg
    from newsroom import db_tools as tools
    from newsroom import league_memory
    def no_unmetered_embeddings(*args, **kwargs):
        raise RuntimeError('Embedding calls are not included in this pilot budget')
    league_memory.embed_text = no_unmetered_embeddings
    from newsroom.run_context import article_run_context
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
    dispatcher = SwarmDispatcher(debug_mode=False)
    evidence = []

    def add(name, **arguments):
        tool = getattr(tools, name)
        result = getattr(tool, 'func', tool)(**arguments)
        rendered = result if isinstance(result, str) else json.dumps(result, default=str)
        status = 'error' if re.search(r'(^Error:|Traceback|does not exist|UndefinedColumn)', rendered) else 'available'
        if not result or re.match(r'(No |Not |Unknown |.*not found)', rendered):
            status = 'missing'
        evidence.append(dict(source=name, arguments=arguments, result=result, status=status))
        return result

    with article_run_context(2036, week), psycopg.connect(dsn) as conn:
        tid, name = conn.execute("SELECT id,name FROM teams WHERE abbreviation='CAR'").fetchone()
        oid, oname = conn.execute('SELECT id,name FROM teams WHERE abbreviation=%s', (opponent,)).fetchone()
        game = conn.execute('SELECT id FROM games WHERE season=2036 AND week=%s AND ((home_team_id=%s AND away_team_id=%s) OR (home_team_id=%s AND away_team_id=%s))', (week, tid, oid, oid, tid)).fetchone()
        if not game:
            raise ValueError('Assignment has no matching scheduled game')
        writer = conn.execute("SELECT agent_id,archetype,persistent_memory,handle,display_name FROM agent_memories WHERE assigned_team_id=%s AND archetype='LOCAL_BEAT_REPORTER' ORDER BY agent_id LIMIT 1", (tid,)).fetchone()
        brief = build_weekly_assignment_brief(dsn, 2036, week)
        prompt = dispatcher.compile_agent_prompt(agent_id=writer[0], archetype=writer[1], persistent_memory=writer[2], handle=writer[3], display_name=writer[4], assigned_team_id=tid, brief=brief, season=2036, week=week, content_type='ARTICLE', article_type='LOCAL_MATCHUP_PRIMER', slot_target=dict(team_id=tid, team_name=name, opponent_team_id=oid, opponent_name=oname, game_id=game[0], note=f'Matchup preview: CAR vs {opponent}'))
        add('get_matchup_edges', team_identifier='CAR', opponent_identifier=opponent, season=2036)
        for abbr, team_id in [('CAR', tid), (opponent, oid)]:
            for helper in ['get_team_efficiency_profile', 'get_coach_profile', 'get_team_season_schedule', 'get_clutch_profile', 'get_league_transactions', 'get_trade_rumors', 'get_team_cap_sheet', 'get_team_roster_depth_chart']:
                add(helper, team_identifier=abbr, season=2036)
            add('get_team_injury_report', team_identifier=abbr, season=2036, week=week)
            add('get_team_context', team_identifier=abbr)
            add('get_team_history', team_identifier=abbr)
            add('get_record_book', team_identifier=abbr, season=2036)
            for category in ['sacks', 'interceptions', 'field_goals']:
                add('get_defense_special_teams_leaders', team_identifier=abbr, season=2036, category=category, limit=5)
            # All roster players: named identities/production, with focused splits/contracts
            # for the top QB, back and receiver. Unknown categories remain unknown.
            players = conn.execute("SELECT p.id,coalesce(p.full_name,p.player_name),ps.position FROM player_seasons ps JOIN players p ON p.id=ps.player_id WHERE ps.season=2036 AND ps.team_id=%s AND (ps.released_week IS NULL OR ps.released_week>%s) AND ps.position IN ('QB','HB','WR','TE') ORDER BY ps.overall_rating DESC NULLS LAST,p.id", (team_id, week)).fetchall()
            selected = []
            for position in ['QB', 'HB', 'WR', 'TE']:
                selected.extend([p for p in players if p[2] == position][:1])
            for pid, player_name, position in selected:
                for helper in ['get_player_context', 'get_player_season_stats', 'get_player_scheme_splits', 'get_player_contract']:
                    add(helper, player_identifier=str(pid))
            previous = conn.execute("SELECT id FROM games WHERE season=2036 AND week<%s AND status='COMPLETED' AND (home_team_id=%s OR away_team_id=%s) ORDER BY week DESC,id DESC LIMIT 1", (week, team_id, team_id)).fetchone()
            if previous:
                add('get_game_box_score', game_id=previous[0])
                add('get_advanced_game_analytics', game_id=previous[0])
        # Empty memory needs no embedding; nonempty index fails closed.
        # Existing writer memory and prior takes are already in the shared system prompt.
        # Freeze actual retrieved league memory as a separate provenance-bearing source.
        add('search_league_memory', query=f'Panthers {oname} rivalry history', team='CAR', top_k=5)
    errors = [e for e in evidence if e['status'] == 'error']
    if errors:
        raise ValueError('Evidence helper failures: ' + ', '.join(e['source'] for e in errors))
    rendered = '\n\n'.join(f"SOURCE {i}: {e['source']} {json.dumps(e['arguments'])}\n{e['result']}" for i, e in enumerate(evidence))
    packet = dict(schema_version=3, database=database, season=2036, week=week, game_id=game[0], opponent=opponent, writer=writer[0], evidence=evidence,
                  shared_limitations=['Archive snapshot values are shared, not reconstructed historical snapshots', 'Dossier coverage is an explicit pilot selection, not exhaustive research', 'Human factual review required'],
                  a=dict(system_instruction=prompt['system_instruction']+'\n'+POLICY, task_instruction=prompt['task_instruction']),
                  b=dict(system_instruction=dossier_instructions(prompt['system_instruction'])+'\n'+POLICY,
                         task_instruction=dossier_instructions(prompt['task_instruction'])+'\n\nCANONICAL EVIDENCE DOSSIER:\n'+rendered+'\n\nWrite the assigned article using supplied evidence. No tools are available. Do not invent canonical facts; creative texture is permitted where it does not contradict data.'))
    return packet, dispatcher.tools_for('LOCAL_MATCHUP_PRIMER')


class Budget:
    """Reserve full model-limit cost per request; retain reservation on uncertain failure.

    The same reservation/settlement applies to Gemini and DeepSeek. DeepSeek's
    rate is chosen by each call's UTC time (peak or off-peak).
    """
    def __init__(self, limit, path, max_output, provider='gemini', input_limit=None):
        self.limit, self.path, self.max_output = limit, path, max_output
        self.provider = provider
        self.input_limit = input_limit or (DEEPSEEK_INPUT_LIMIT if provider == 'deepseek' else 1048576)
        self.rows = []

    def document(self):
        if self.provider == 'deepseek':
            return dict(limit=self.limit, provider=self.provider, timezone='UTC',
                        prices=dict(peak=DEEPSEEK_PEAK, off_peak=DEEPSEEK_OFF_PEAK),
                        peak_periods='Mon-Fri 01:00-04:00 and 06:00-10:00 UTC',
                        holiday_note='Chinese public holidays are off-peak on the provider price list; no holiday calendar is kept, so those weekdays are priced as peak',
                        source=DEEPSEEK_PRICE_SOURCE, checked=DEEPSEEK_PRICE_CHECKED, calls=self.rows)
        return dict(limit=self.limit, provider=self.provider, prices=PRICES, source=PRICE_SOURCE, calls=self.rows)

    def reserve(self, at=None):
        # Entire model input context, no cache discount; double output reservation
        # conservatively covers thought/output reporting differences.
        row = dict(status='reserved', provider=self.provider)
        if self.provider == 'deepseek':
            when = at or dt.datetime.now(dt.timezone.utc)
            rates, period = deepseek_rates(when)
            bound = (self.input_limit * rates['uncached'] + 2 * self.max_output * rates['output']) / 1e6
            row.update(priced_at=when.isoformat(), price_period=period, prices=rates)
        else:
            bound = (self.input_limit * PRICES['input'] + 2 * self.max_output * PRICES['output']) / 1e6
        if sum(r['charged_or_reserved'] for r in self.rows) + bound > self.limit:
            raise RuntimeError('Spend ceiling: insufficient remaining budget for another request')
        row['charged_or_reserved'] = bound
        self.rows.append(row)
        dump(self.path, self.document())
        return row

    def complete(self, row, response):
        if self.provider == 'deepseek':
            from newsroom.tool_loop.openai_loop import extract_openai_usage
            usage_obj = getattr(response, 'usage', None)
            if usage_obj is None:
                raise RuntimeError('Missing usage: reserved cost retained; stopping')
            usage = extract_openai_usage(usage_obj)
            row.update(status='completed', usage=usage,
                       charged_or_reserved=cost(usage, provider='deepseek', rates=row.get('prices')))
        else:
            from db.llm_usage import _usage
            usage = _usage(response)
            if response.usage_metadata is None:
                raise RuntimeError('Missing usage: reserved cost retained; stopping')
            row.update(status='completed', usage=usage, charged_or_reserved=cost(usage))
        dump(self.path, self.document())


def run_arm(packet, arm, tools, model, budget, directory):
    from google import genai
    from google.genai import types
    from engines.gm_llm import api_key
    from engines.llm_config import make_content_config
    from newsroom.run_context import article_run_context
    from tools.dossier_ab_compare import audit_article_quality
    key = api_key()
    if not key:
        raise RuntimeError('No API key configured')
    first_call = len(budget.rows)
    trace = []
    exposed = []
    for tool in tools if arm == 'a' else []:
        fn = getattr(tool, 'func', tool)
        def wrap(fn):
            @functools.wraps(fn)
            def call(*args, **kwargs):
                try:
                    value = fn(*args, **kwargs)
                except Exception as exc:
                    value = f'Tool Error: {exc}'
                trace.append(dict(name=fn.__name__, args=args, kwargs=kwargs, result=value))
                dump(directory / f'{arm}_tool_trace.json', trace)
                return value if isinstance(value, str) else json.dumps(value, default=str)
            return call
        exposed.append(wrap(fn))
    config, clean_model = make_content_config(model=model, temperature=0.7, max_output_tokens=budget.max_output,
        system_instruction=packet[arm]['system_instruction'], tools=exposed or None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=25))
    # Same AFC behavior as baseline, with explicit settings and per-request accounting.
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120000, retry_options=types.HttpRetryOptions(attempts=1)))
    metadata = client.models.get(model=clean_model)
    if metadata.input_token_limit and metadata.input_token_limit > 1048576:
        raise RuntimeError('Model input limit exceeds verified budget reservation')
    dump(directory / f'{arm}_model_metadata.json', metadata.model_dump(mode='json'))
    original = client.models._generate_content
    def metered(*args, **kwargs):
        row = budget.reserve()
        row['arm'] = arm
        started = time.perf_counter()
        response = original(*args, **kwargs)
        budget.complete(row, response)
        row['latency_s'] = time.perf_counter() - started
        row['arm'] = arm
        dump(budget.path, budget.document())
        dump(directory / f'{arm}_last_response.json', response.model_dump(mode='json'))
        return response
    client.models._generate_content = metered
    started = time.perf_counter()
    result = dict(arm=arm, model=model, status='failed', text='', factual_status='unreviewed')
    try:
        with article_run_context(packet['season'], packet['week']):
            chat = client.chats.create(model=clean_model, config=config)
            response = chat.send_message(packet[arm]['task_instruction'])
            if not response.text:
                response = chat.send_message('Using gathered evidence, write the complete assigned article now. Do not call further tools.')
            if not response.text:
                raise RuntimeError('Empty final article')
            result.update(status='generated', text=response.text, format_audit=audit_article_quality(response.text))
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        client.close()
        result['latency_s'] = time.perf_counter() - started
        result['tool_calls'] = len(trace)
        result['usage_calls'] = [r for r in budget.rows[first_call:] if r.get('arm') == arm]
        result['estimated_cost_usd'] = sum(r['charged_or_reserved'] for r in result['usage_calls'])
        dump(directory / f'{arm}_result.json', result)
    if result['status'] != 'generated':
        raise RuntimeError(result.get('error', 'Generation failed'))
    return result


def resolve_budget(provider, budget_usd):
    """Explicit ceiling wins; DeepSeek defaults to $1.00, Gemini needs one for live."""
    if budget_usd is not None:
        return budget_usd
    return 1.0 if provider == 'deepseek' else 0


def deepseek_client():
    """Build the DeepSeek OpenAI-compatible client with the pilot's retry policy."""
    from newsroom.tool_loop.openai_loop import build_default_client
    return build_default_client('deepseek', max_retries=0)


def run_deepseek_arm(packet, arm, tools, model, budget, directory, client=None):
    """DeepSeek arm: ReAct through the OpenAI-compatible tool loop, or one dossier call.

    Settings match the Gemini arm: same writer/system prompt, cutoff, packet,
    temperature 0.7 and 8,192 output cap. DeepSeek runs in thinking mode by
    default and its docs say temperature has no effect there, so the recording
    keeps the value for parity but notes that the provider may ignore it.
    """
    from newsroom.tool_loop.openai_loop import run_openai_tool_loop
    from newsroom.run_context import article_run_context
    from tools.dossier_ab_compare import audit_article_quality
    if client is None:
        client = deepseek_client()
    first_call = len(budget.rows)
    trace = []
    exposed = []
    for tool in tools if arm == 'a' else []:
        fn = getattr(tool, 'func', tool)
        def wrap(fn):
            @functools.wraps(fn)
            def call(*args, **kwargs):
                try:
                    value = fn(*args, **kwargs)
                except Exception as exc:
                    value = f'Tool Error: {exc}'
                trace.append(dict(name=fn.__name__, args=args, kwargs=kwargs, result=value))
                dump(directory / f'{arm}_tool_trace.json', trace)
                return value if isinstance(value, str) else json.dumps(value, default=str)
            return call
        exposed.append(wrap(fn))
    completions = client.chat.completions
    original = completions.create
    def metered(**kwargs):
        row = budget.reserve()
        row['arm'] = arm
        started = time.perf_counter()
        response = original(**kwargs)
        choices = getattr(response, 'choices', None) or []
        row['finish_reason'] = getattr(choices[0], 'finish_reason', None) if choices else None
        budget.complete(row, response)
        row['latency_s'] = time.perf_counter() - started
        row['arm'] = arm
        dump(budget.path, budget.document())
        dump(directory / f'{arm}_last_response.json',
             response.model_dump(mode='json') if hasattr(response, 'model_dump') else str(response))
        return response
    completions.create = metered
    started = time.perf_counter()
    result = dict(arm=arm, provider='deepseek', model=model, status='failed', text='', factual_status='unreviewed')
    try:
        with article_run_context(packet['season'], packet['week']):
            if arm == 'a':
                loop = run_openai_tool_loop(client, model, packet['a']['system_instruction'],
                    packet['a']['task_instruction'], exposed, max_steps=25, temperature=TEMPERATURE,
                    provider='deepseek', max_tokens=budget.max_output)
                result['iterations'] = loop.iterations
                result['tool_calls_by_name'] = dict(loop.tool_calls_count)
                if loop.error and not loop.text.strip():
                    raise RuntimeError(loop.error)
                text = loop.text
                # The last metered call produced the article: the final turn, or
                # the synthesis follow-up when the research turn returned no text.
                finish = loop.usage_calls[-1].get('finish_reason') if loop.usage_calls else None
            else:
                response = completions.create(model=model, messages=[
                    dict(role='system', content=packet['b']['system_instruction']),
                    dict(role='user', content=packet['b']['task_instruction'])],
                    temperature=TEMPERATURE, max_tokens=budget.max_output)
                result['iterations'] = 1
                text = response.choices[0].message.content or ''
                finish = getattr(response.choices[0], 'finish_reason', None)
            result['finish_reason'] = finish
            if not text.strip():
                raise RuntimeError('Empty final article')
            if finish == 'length':
                # Thinking mode shares max_tokens with the article, so a long think
                # can return a cut-off article that still passes the non-empty check.
                result.update(status='truncated', text=text, format_audit=audit_article_quality(text))
                raise RuntimeError('Truncated article: finish_reason=length, the model hit the output-token cap')
            result.update(status='generated', text=text, format_audit=audit_article_quality(text))
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['latency_s'] = time.perf_counter() - started
        result['tool_calls'] = len(trace)
        result['usage_calls'] = [r for r in budget.rows[first_call:] if r.get('arm') == arm]
        result['estimated_cost_usd'] = sum(r['charged_or_reserved'] for r in result['usage_calls'])
        result['settings_note'] = 'temperature 0.7 requested; DeepSeek runs thinking mode by default and its docs say temperature has no effect there'
        dump(directory / f'{arm}_result.json', result)
    if result['status'] != 'generated':
        raise RuntimeError(result.get('error', 'Generation failed'))
    return result


def review_sheet(directory, articles):
    with (directory / 'factual_review.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['sample', 'paragraph', 'text', 'claim', 'source_reference', 'expected_value', 'verdict', 'reviewer_note'])
        for index, article in enumerate(articles):
            sample = hashlib.sha256(f"{directory.name}:{index}".encode()).hexdigest()[:10]
            (directory / f'sample_{sample}.md').write_text(article['text'])
            for paragraph, text in enumerate(article['text'].split('\n\n'), 1):
                if text.strip():
                    writer.writerow([sample, paragraph, text, '', '', '', 'unreviewed', ''])
    dump(directory / 'review_key.json', {hashlib.sha256(f"{directory.name}:{i}".encode()).hexdigest()[:10]: a['arm'] for i, a in enumerate(articles)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--provider', choices=['gemini', 'deepseek'], default='gemini')
    parser.add_argument('--budget-usd', type=float, default=None,
                        help='Spend ceiling for live calls (DeepSeek default: $1.00)')
    parser.add_argument('--model', default=None)
    parser.add_argument('--week', type=int, choices=[2, 12, 21], default=12)
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    model = args.model or (DEEPSEEK_MODEL if args.provider == 'deepseek' else GEMINI_MODEL)
    budget_usd = resolve_budget(args.provider, args.budget_usd)
    if args.live:
        if args.provider == 'gemini' and (budget_usd <= 0 or model != GEMINI_MODEL or dt.date.today() > dt.date(2026, 12, 31)):
            parser.error('Live requires positive budget and the verified model/rate period')
        if args.provider == 'deepseek':
            if budget_usd <= 0:
                parser.error('Live requires a positive budget')
            if model != DEEPSEEK_MODEL:
                parser.error('DeepSeek live runs are pinned to deepseek-flash')
            try:
                from newsroom.tool_loop.openai_loop import resolve_endpoint
                resolve_endpoint('deepseek')
            except ValueError as exc:
                parser.error(f'DeepSeek live requires DEEPSEEK_API_KEY: {exc}')
    if args.repeats < 1:
        parser.error('repeats must be positive')
    dsn, database = configure_database()
    directory = Path('verify_output/v3_pilot') / dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    start = time.perf_counter()
    packet, tools = prepare(dsn, database, args.week, {2:'PIT', 12:'MIN', 21:'AZ'}[args.week])
    packet['compiler_seconds'] = time.perf_counter() - start
    packet['provider'] = args.provider
    packet['model'] = model
    packet['requested_temperature'] = TEMPERATURE
    packet['output_token_cap'] = 8192
    if args.provider == 'deepseek':
        packet['settings_note'] = ('DeepSeek deepseek-flash runs in thinking mode by default; its API docs state '
            'temperature has no effect in thinking mode, so 0.7 is recorded for parity but may be ignored. '
            'Both arms share the writer prompt, cutoff and 8,192 output cap.')
        packet['pricing'] = dict(source=DEEPSEEK_PRICE_SOURCE, checked=DEEPSEEK_PRICE_CHECKED,
            peak=DEEPSEEK_PEAK, off_peak=DEEPSEEK_OFF_PEAK,
            peak_periods='Mon-Fri 01:00-04:00 and 06:00-10:00 UTC',
            holiday_note='Chinese public holidays are off-peak on the provider price list; no holiday calendar is kept, so those weekdays are priced as peak',
            input_token_limit=DEEPSEEK_INPUT_LIMIT)
    packet['harness_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    packet['editorial_adjustments'] = 'Original creative-texture directives retained in both arms; shared policy permits invention that does not contradict canonical data. Production configuration unchanged.'
    correction = Path('verify_output/v3_pilot/evaluation_corrections.json')
    packet['evaluation_corrections'] = json.loads(correction.read_text()) if correction.exists() else None
    packet['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    packet['code_diff_sha256'] = hashlib.sha256(subprocess.check_output(['git', 'diff'])).hexdigest()
    dump(directory / 'packet.json', packet)
    (directory / 'packet.sha256').write_text(hashlib.sha256((directory / 'packet.json').read_bytes()).hexdigest())
    print(f'Prepared {directory}: {len(packet["evidence"])} evidence sections', flush=True)
    if not args.live:
        return
    budget = Budget(budget_usd, directory / 'usage.json', 8192, provider=args.provider)
    run = run_deepseek_arm if args.provider == 'deepseek' else run_arm
    for repeat in range(args.repeats):
        pair_dir = directory / f'pair_{repeat+1}'
        pair_dir.mkdir()
        articles = []
        for arm in ('a', 'b') if repeat % 2 == 0 else ('b', 'a'):
            print(f'Generating pair {repeat+1}, arm {arm}', flush=True)
            articles.append(run(packet, arm, tools, model, budget, pair_dir))
        review_sheet(pair_dir, articles)
    print(f'Pilot generated; factual review required: {directory}', flush=True)


if __name__ == '__main__':
    main()
