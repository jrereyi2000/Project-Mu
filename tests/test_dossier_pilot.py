import datetime as dt
import json
from types import SimpleNamespace
import pytest
from tools.dossier_pilot import (Budget, configure_database, cost, deepseek_rates,
                                 dossier_instructions, resolve_budget, run_deepseek_arm,
                                 DEEPSEEK_MODEL, DEEPSEEK_OFF_PEAK, DEEPSEEK_PEAK)


def test_cost_includes_thinking_and_cache():
    usage = dict(input_tokens=1000, cached_tokens=400, output_tokens=200, thinking_tokens=300)
    assert cost(usage) == pytest.approx((600*.75+400*.075+500*3.75)/1e6)


def test_failed_request_reservation_blocks_overspend(tmp_path):
    budget = Budget(1, tmp_path/'usage.json', 8192)
    budget.reserve()
    with pytest.raises(RuntimeError, match='Spend ceiling'):
        budget.reserve()
    assert json.loads((tmp_path/'usage.json').read_text())['calls'][0]['status'] == 'reserved'


def test_success_releases_unused_reservation(tmp_path):
    budget = Budget(1, tmp_path/'usage.json', 8192)
    row = budget.reserve()
    response = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=100, cached_content_token_count=0, candidates_token_count=50, thoughts_token_count=20))
    budget.complete(row, response)
    budget.reserve()
    assert len(budget.rows) == 2


def test_missing_usage_stops_without_releasing_reservation(tmp_path):
    budget = Budget(1, tmp_path/'usage.json', 8192)
    row = budget.reserve()
    bound = row['charged_or_reserved']
    with pytest.raises(RuntimeError, match='Missing usage'):
        budget.complete(row, SimpleNamespace(usage_metadata=None))
    assert row['charged_or_reserved'] == bound


@pytest.mark.parametrize('dsn', ['', 'dbname=madden_2036', 'dbname=madden', 'dbname=madden_test'])
def test_rejects_nonpilot_database(monkeypatch, dsn):
    monkeypatch.setenv('DATABASE_URL', dsn)
    with pytest.raises(ValueError, match='scratch'):
        configure_database()


def test_rewrites_access_but_keeps_editorial_rules():
    source = 'ROLE: writer\nAVAILABLE DATABASE REAC/TOOLS:\ncall tools\nWEEK CONTEXT:\nDo not invent injuries.\nCall `get_coach_profile`.\n- Query the schedule.\n'
    rewritten = dossier_instructions(source)
    assert 'Do not invent injuries.' in rewritten
    assert 'ROLE: writer' in rewritten
    assert 'call tools' not in rewritten
    assert 'Call `get_coach_profile`' not in rewritten
    assert '- Query' not in rewritten
    assert 'No tools are available' in rewritten

def test_live_arm_uses_same_explicit_settings_and_meter(monkeypatch, tmp_path):
    from google import genai
    from engines import gm_llm
    from newsroom.run_context import context_week
    from tools.dossier_pilot import run_arm
    seen = []
    response = SimpleNamespace(text='# Title\n\n> **EXCERPT:** Summary\n\nEvidence-led article.',
        usage_metadata=SimpleNamespace(prompt_token_count=100, cached_content_token_count=0, candidates_token_count=50, thoughts_token_count=10),
        model_dump=lambda **kwargs: {'text': 'article'})
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['http_options'].retry_options.attempts == 1
            self.models = SimpleNamespace(
                get=lambda **kwargs: SimpleNamespace(input_token_limit=1048576, model_dump=lambda **kwargs: {}),
                _generate_content=self.generate)
            self.chats = SimpleNamespace(create=self.create)
        def generate(self, *args, **kwargs):
            assert context_week() == 21
            return response
        def create(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(send_message=lambda prompt: self.models._generate_content())
        def close(self):
            pass
    monkeypatch.setattr(genai, 'Client', FakeClient)
    monkeypatch.setattr(gm_llm, 'api_key', lambda: 'fake-key')
    packet = {'season':2036, 'week':21, 'a':{'system_instruction':'writer','task_instruction':'article'}, 'b':{'system_instruction':'writer','task_instruction':'article'}}
    budget = Budget(2, tmp_path/'usage.json', 8192)
    for arm in ['a','b']:
        result = run_arm(packet, arm, [], 'gemini-3.8-flash:low', budget, tmp_path)
        assert result['status'] == 'generated'
        assert result['factual_status'] == 'unreviewed'
    assert len(budget.rows) == 2
    for call in seen:
        assert call['model'] == 'gemini-3.8-flash'
        assert call['config'].temperature == .7
        assert call['config'].max_output_tokens == 8192
        assert call['config'].thinking_config.thinking_level == 'LOW'

def test_deepseek_rates_follow_utc_peak_windows():
    def at(year, month, day, hour, minute=0):
        return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    # Wednesday 2026-11-11
    assert deepseek_rates(at(2026, 11, 11, 1, 0)) == (DEEPSEEK_PEAK, 'peak')
    assert deepseek_rates(at(2026, 11, 11, 3, 59)) == (DEEPSEEK_PEAK, 'peak')
    assert deepseek_rates(at(2026, 11, 11, 4, 0)) == (DEEPSEEK_OFF_PEAK, 'off-peak')
    assert deepseek_rates(at(2026, 11, 11, 5, 59)) == (DEEPSEEK_OFF_PEAK, 'off-peak')
    assert deepseek_rates(at(2026, 11, 11, 6, 0)) == (DEEPSEEK_PEAK, 'peak')
    assert deepseek_rates(at(2026, 11, 11, 9, 59)) == (DEEPSEEK_PEAK, 'peak')
    assert deepseek_rates(at(2026, 11, 11, 10, 0)) == (DEEPSEEK_OFF_PEAK, 'off-peak')
    # Saturday is off-peak all day.
    assert deepseek_rates(at(2026, 11, 14, 2, 0)) == (DEEPSEEK_OFF_PEAK, 'off-peak')


def test_deepseek_cost_prices_cache_miss_and_hit_separately():
    usage = dict(input_tokens=1000, cached_tokens=400, uncached_tokens=600,
                 output_tokens=200, thinking_tokens=100)
    expected = (600 * .30 + 400 * .006 + 200 * 1.20) / 1e6
    assert cost(usage, provider='deepseek', rates=DEEPSEEK_PEAK) == pytest.approx(expected)
    assert cost(usage, provider='deepseek', rates=DEEPSEEK_OFF_PEAK) == pytest.approx(expected / 2)


def test_deepseek_reasoning_tokens_are_not_billed_twice():
    base = dict(input_tokens=1000, cached_tokens=0, uncached_tokens=1000, output_tokens=200)
    with_thinking = dict(base, thinking_tokens=150)
    assert cost(with_thinking, provider='deepseek', rates=DEEPSEEK_PEAK) == \
        pytest.approx(cost(base, provider='deepseek', rates=DEEPSEEK_PEAK))


def test_deepseek_cost_prefers_provider_reported_list_cost():
    """A Go response's own list cost wins over the local table (§2.6)."""
    usage = dict(input_tokens=1000, cached_tokens=400, uncached_tokens=600,
                 output_tokens=200, thinking_tokens=100,
                 cost_usd=0.0, list_cost_usd=0.01234, allowance_usd=0.0074,
                 billing_source='go')
    assert cost(usage, provider='deepseek') == pytest.approx(0.01234)
    local = (600 * .30 + 400 * .006 + 200 * 1.20) / 1e6
    assert cost(usage, provider='deepseek') != pytest.approx(local)
    # A reported zero is a free call, not a missing field: it is not repriced locally.
    assert cost(dict(usage, list_cost_usd=0.0), provider='deepseek') == 0


def test_deepseek_cost_falls_back_to_local_table_without_reported_cost():
    usage = dict(input_tokens=1000, cached_tokens=400, uncached_tokens=600,
                 output_tokens=200, thinking_tokens=100, list_cost_usd=None)
    expected = (600 * .30 + 400 * .006 + 200 * 1.20) / 1e6
    assert cost(usage, provider='deepseek', rates=DEEPSEEK_PEAK) == pytest.approx(expected)
    assert cost(usage, provider='deepseek', rates=DEEPSEEK_OFF_PEAK) == pytest.approx(expected / 2)
    # A direct DeepSeek response has no such key at all; that behaves the same.
    without_key = {k: v for k, v in usage.items() if k != 'list_cost_usd'}
    assert cost(without_key, provider='deepseek', rates=DEEPSEEK_PEAK) == pytest.approx(expected)


def test_deepseek_budget_reserves_peak_or_offpeak_by_call_time(tmp_path):
    peak_at = dt.datetime(2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc)
    weekend_at = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.timezone.utc)
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    peak_row = budget.reserve(at=peak_at)
    weekend_row = budget.reserve(at=weekend_at)
    assert peak_row['price_period'] == 'peak'
    assert weekend_row['price_period'] == 'off-peak'
    assert peak_row['charged_or_reserved'] == pytest.approx((1_000_000 * .30 + 2 * 8192 * 1.20) / 1e6)
    assert weekend_row['charged_or_reserved'] == pytest.approx(peak_row['charged_or_reserved'] / 2)
    document = json.loads((tmp_path / 'usage.json').read_text())
    assert document['source'] == 'https://api-docs.deepseek.com/quick_start/pricing'
    assert document['checked'] == '2026-11-11'


def test_deepseek_budget_settles_openai_usage(tmp_path):
    peak_at = dt.datetime(2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc)
    budget = Budget(1, tmp_path / 'usage.json', 8192, provider='deepseek')
    row = budget.reserve(at=peak_at)
    response = SimpleNamespace(usage={
        'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 600,
        'prompt_cache_miss_tokens': 400, 'completion_tokens': 200,
        'completion_tokens_details': {'reasoning_tokens': 80}})
    budget.complete(row, response)
    assert row['status'] == 'completed'
    assert row['usage'] == dict(input_tokens=1000, cached_tokens=600, uncached_tokens=400,
                                output_tokens=200, thinking_tokens=80,
                                cost_usd=None, list_cost_usd=None, allowance_usd=None,
                                billing_source=None)
    assert row['price_source'] == 'local-peak'
    assert row['charged_or_reserved'] == pytest.approx(
        cost(row['usage'], provider='deepseek', rates=DEEPSEEK_PEAK))


def test_deepseek_budget_settles_from_go_reported_cost_and_keeps_ceiling(tmp_path):
    """Settlement uses Go's list cost, and the ceiling still holds over it."""
    peak_at = dt.datetime(2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc)
    # 1,000 input tokens bound plus double the 8,192 output cap reserves $0.0199608.
    budget = Budget(0.05, tmp_path / 'usage.json', 8192, provider='deepseek', input_limit=1000)
    row = budget.reserve(at=peak_at)
    assert row['charged_or_reserved'] == pytest.approx((1000 * .30 + 2 * 8192 * 1.20) / 1e6)
    response = SimpleNamespace(usage={
        'prompt_tokens': 100, 'completion_tokens': 40, 'cost': 0.0,
        'opencode': {'list_cost': 0.0123, 'allowance': 0.0074, 'billing_source': 'go'}})
    budget.complete(row, response)
    assert row['status'] == 'completed'
    assert row['usage']['list_cost_usd'] == pytest.approx(0.0123)
    assert row['usage']['billing_source'] == 'go'
    assert row['charged_or_reserved'] == pytest.approx(0.0123)
    assert row['price_source'] == 'provider-list-cost'
    # This call stayed inside the ceiling, so one more reservation still fits.
    budget.reserve(at=peak_at)
    assert len(budget.rows) == 2
    document = json.loads((tmp_path / 'usage.json').read_text())
    assert 'reported_cost_note' in document

    # A price the provider reports above the ceiling still stops the run: the
    # reservation fits, the settlement does not, and the next request is refused.
    tight = Budget(0.03, tmp_path / 'tight.json', 8192, provider='deepseek', input_limit=1000)
    spent = tight.reserve(at=peak_at)
    tight.complete(spent, SimpleNamespace(usage={
        'prompt_tokens': 100, 'completion_tokens': 40,
        'opencode': {'list_cost': 0.05, 'allowance': 0.03, 'billing_source': 'go'}}))
    assert spent['charged_or_reserved'] == pytest.approx(0.05)
    assert sum(r['charged_or_reserved'] for r in tight.rows) > tight.limit
    with pytest.raises(RuntimeError, match='Spend ceiling'):
        tight.reserve(at=peak_at)


def test_deepseek_missing_usage_retains_reservation(tmp_path):
    budget = Budget(1, tmp_path / 'usage.json', 8192, provider='deepseek')
    row = budget.reserve()
    bound = row['charged_or_reserved']
    with pytest.raises(RuntimeError, match='Missing usage'):
        budget.complete(row, SimpleNamespace(usage=None))
    assert row['charged_or_reserved'] == bound
    assert row['status'] == 'reserved'


def test_deepseek_budget_defaults_to_one_dollar():
    assert resolve_budget('deepseek', None) == 1.0
    assert resolve_budget('gemini', None) == 0
    assert resolve_budget('deepseek', 2.5) == 2.5


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _deepseek_response(content=None, tool_calls=None, reasoning='research plan', usage=None,
                       finish_reason='stop'):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    if reasoning is not None:
        message.reasoning_content = reasoning
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage or {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 600,
                        'prompt_cache_miss_tokens': 400, 'completion_tokens': 200,
                        'completion_tokens_details': {'reasoning_tokens': 50}},
        model_dump=lambda **kwargs: {'id': 'fake'})


def _get_matchup_edges(team_identifier: str = 'CAR') -> str:
    """Fetch matchup edges."""
    return 'edges: CAR +2.1'


def test_deepseek_react_arm_uses_openai_loop_and_meters_each_call(tmp_path):
    tool_call = SimpleNamespace(id='call_1', type='function',
                                function=SimpleNamespace(name='_get_matchup_edges', arguments='{}'))
    client = _FakeClient([_deepseek_response(tool_calls=[tool_call]),
                          _deepseek_response(content='# Game\n\n> **EXCERPT:** Both teams.\n\nBody.')])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    packet = {'season': 2036, 'week': 21,
              'a': {'system_instruction': 'writer', 'task_instruction': 'article'},
              'b': {'system_instruction': 'writer', 'task_instruction': 'dossier'}}
    result = run_deepseek_arm(packet, 'a', [_get_matchup_edges], DEEPSEEK_MODEL,
                              budget, tmp_path, client=client)
    calls = client.completions.calls
    assert result['status'] == 'generated'
    assert result['provider'] == 'deepseek'
    assert len(calls) == 2
    # The loop translates the deprecated `deepseek-flash` id to the Go model id
    # (OPENCODE_GO_PIPELINE_PLAN.md §2.2); the pilot still records the requested name.
    assert calls[0]['model'] == 'deepseek-v4.1-flash'
    assert result['model'] == DEEPSEEK_MODEL
    assert result['model_sent'] == 'deepseek-v4.1-flash'
    assert calls[0]['temperature'] == .7
    assert calls[0]['max_tokens'] == 8192
    assert calls[0]['tools']
    assert calls[1]['messages'][2]['reasoning_content'] == 'research plan'
    assert len(result['usage_calls']) == 2
    assert result['usage_calls'][0]['usage']['uncached_tokens'] == 400
    assert budget.rows[0]['status'] == 'completed'
    assert (tmp_path / 'a_result.json').exists()
    assert (tmp_path / 'a_tool_trace.json').exists()


def test_deepseek_dossier_arm_meters_go_reported_cost(monkeypatch, tmp_path):
    """A Go response is settled at its list cost, metered per call and ledgered."""
    monkeypatch.setenv('MU_GO_ALLOWANCE_FILE', str(tmp_path / 'go_allowance.jsonl'))
    usage = {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 600,
             'prompt_cache_miss_tokens': 400, 'completion_tokens': 200,
             'completion_tokens_details': {'reasoning_tokens': 50}, 'cost': 0.0,
             'opencode': {'list_cost': 0.008, 'allowance': 0.0048, 'billing_source': 'go'}}
    client = _FakeClient([_deepseek_response(
        content='# Game\n\n> **EXCERPT:** Both teams.\n\nBody.', usage=usage)])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    result = run_deepseek_arm(_packet(), 'b', [_get_matchup_edges], DEEPSEEK_MODEL,
                              budget, tmp_path, client=client)
    assert result['status'] == 'generated'
    call = result['usage_calls'][0]
    assert len(result['usage_calls']) == 1
    assert call['usage']['list_cost_usd'] == pytest.approx(0.008)
    assert call['usage']['allowance_usd'] == pytest.approx(0.0048)
    assert call['usage']['billing_source'] == 'go'
    assert call['price_source'] == 'provider-list-cost'
    assert call['finish_reason'] == 'stop'
    assert result['estimated_cost_usd'] == pytest.approx(0.008)
    ledger = (tmp_path / 'go_allowance.jsonl').read_text().splitlines()
    assert len(ledger) == 1
    assert json.loads(ledger[0])['allowance_usd'] == pytest.approx(0.0048)


def test_deepseek_dossier_arm_is_one_call_without_tools(tmp_path):
    client = _FakeClient([_deepseek_response(
        content='# Game\n\n> **EXCERPT:** Both teams.\n\nBody.')])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    packet = {'season': 2036, 'week': 21,
              'a': {'system_instruction': 'writer', 'task_instruction': 'article'},
              'b': {'system_instruction': 'writer', 'task_instruction': 'dossier'}}
    result = run_deepseek_arm(packet, 'b', [_get_matchup_edges], DEEPSEEK_MODEL,
                              budget, tmp_path, client=client)
    call = client.completions.calls[0]
    assert call['model'] == 'deepseek-v4.1-flash'
    assert 'tools' not in call
    assert call['max_tokens'] == 8192
    assert [m['role'] for m in call['messages']] == ['system', 'user']
    assert result['tool_calls'] == 0
    assert result['iterations'] == 1
    assert result['status'] == 'generated'
    assert result['usage_calls'][0]['price_period'] in ('peak', 'off-peak')


def _packet():
    return {'season': 2036, 'week': 21,
            'a': {'system_instruction': 'writer', 'task_instruction': 'article'},
            'b': {'system_instruction': 'writer', 'task_instruction': 'dossier'}}


def test_deepseek_react_arm_flags_truncated_article(tmp_path):
    client = _FakeClient([_deepseek_response(content='# Game\n\n> **EXCERPT:** Cut off mid-senten',
                                             finish_reason='length')])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    with pytest.raises(RuntimeError, match='Truncated article'):
        run_deepseek_arm(_packet(), 'a', [_get_matchup_edges], DEEPSEEK_MODEL,
                         budget, tmp_path, client=client)
    result = json.loads((tmp_path / 'a_result.json').read_text())
    assert result['status'] == 'truncated'
    assert result['finish_reason'] == 'length'
    assert result['usage_calls'][0]['finish_reason'] == 'length'
    assert result['estimated_cost_usd'] > 0


def test_deepseek_dossier_arm_flags_truncated_article(tmp_path):
    client = _FakeClient([_deepseek_response(content='# Game\n\n> **EXCERPT:** Cut off mid-senten',
                                             finish_reason='length')])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    with pytest.raises(RuntimeError, match='Truncated article'):
        run_deepseek_arm(_packet(), 'b', [_get_matchup_edges], DEEPSEEK_MODEL,
                         budget, tmp_path, client=client)
    result = json.loads((tmp_path / 'b_result.json').read_text())
    assert result['status'] == 'truncated'
    assert result['finish_reason'] == 'length'
    document = json.loads((tmp_path / 'usage.json').read_text())
    assert document['calls'][0]['finish_reason'] == 'length'
    assert budget.rows[0]['status'] == 'completed'


def test_creative_texture_is_allowed_without_canonical_contradictions():
    from tools.dossier_pilot import POLICY
    assert 'Invent quotes, private reporting access, scenes, dialogue and unrecorded plays freely' in POLICY
    assert 'Never contradict recorded scores' in POLICY
    assert 'Unknown canonical numbers remain unknown' in POLICY
    assert 'distinguish tied from broken' in POLICY
    assert 'Do not fabricate quotes' not in POLICY


# --- one injected client must survive both arms unchanged (R4 F3) ------------------------------


def _go_usage():
    return {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 600,
            'prompt_cache_miss_tokens': 400, 'completion_tokens': 200,
            'completion_tokens_details': {'reasoning_tokens': 50}, 'cost': 0.0,
            'opencode': {'list_cost': 0.008, 'allowance': 0.0048, 'billing_source': 'go'}}


def _article():
    return _deepseek_response(content='# Game\n\n> **EXCERPT:** Both teams.\n\nBody.',
                              usage=_go_usage())


def _ledger_rows(tmp_path):
    return (tmp_path / 'go_allowance.jsonl').read_text().splitlines()


def test_a_shared_client_is_restored_after_each_arm(monkeypatch, tmp_path):
    """a then b on one client: one ledger row per API call, not two for arm b."""
    monkeypatch.setenv('MU_GO_ALLOWANCE_FILE', str(tmp_path / 'go_allowance.jsonl'))
    tool_call = SimpleNamespace(id='call_1', type='function',
                                function=SimpleNamespace(name='_get_matchup_edges', arguments='{}'))
    client = _FakeClient([_deepseek_response(tool_calls=[tool_call], usage=_go_usage()),
                          _article(), _article()])
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    original = client.completions.create
    assert original.__name__ == 'create'

    run_deepseek_arm(_packet(), 'a', [_get_matchup_edges], DEEPSEEK_MODEL, budget, tmp_path,
                     client=client)
    assert client.completions.create.__func__ is original.__func__
    run_deepseek_arm(_packet(), 'b', [_get_matchup_edges], DEEPSEEK_MODEL, budget, tmp_path,
                     client=client)
    assert client.completions.create.__func__ is original.__func__

    assert len(budget.rows) == 3                    # a made 2 requests, b made 1
    assert len(_ledger_rows(tmp_path)) == 3
    assert [row['arm'] for row in budget.rows] == ['a', 'a', 'b']


def test_a_shared_client_run_b_then_a_does_not_double_ledger(monkeypatch, tmp_path):
    """b then a on one client: arm a's loop must not sit inside arm b's metered wrapper."""
    monkeypatch.setenv('MU_GO_ALLOWANCE_FILE', str(tmp_path / 'go_allowance.jsonl'))
    tool_call = SimpleNamespace(id='call_1', type='function',
                                function=SimpleNamespace(name='_get_matchup_edges', arguments='{}'))
    client = _FakeClient([_article(),                     # arm b
                          _deepseek_response(tool_calls=[tool_call], usage=_go_usage()),
                          _article()])                    # arm a's two loop requests
    budget = Budget(5, tmp_path / 'usage.json', 8192, provider='deepseek')
    original = client.completions.create
    assert original.__name__ == 'create'

    run_deepseek_arm(_packet(), 'b', [_get_matchup_edges], DEEPSEEK_MODEL, budget, tmp_path,
                     client=client)
    assert client.completions.create.__func__ is original.__func__
    run_deepseek_arm(_packet(), 'a', [_get_matchup_edges], DEEPSEEK_MODEL, budget, tmp_path,
                     client=client)
    assert client.completions.create.__func__ is original.__func__

    assert len(budget.rows) == 3
    assert len(_ledger_rows(tmp_path)) == 3
    assert [row['arm'] for row in budget.rows] == ['b', 'a', 'a']
