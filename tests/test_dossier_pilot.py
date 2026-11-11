import json
from types import SimpleNamespace
import pytest
from tools.dossier_pilot import Budget, configure_database, cost, dossier_instructions


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

def test_creative_texture_is_allowed_without_canonical_contradictions():
    from tools.dossier_pilot import POLICY
    assert 'Invent quotes, private reporting access, scenes, dialogue and unrecorded plays freely' in POLICY
    assert 'Never contradict recorded scores' in POLICY
    assert 'Unknown canonical numbers remain unknown' in POLICY
    assert 'distinguish tied from broken' in POLICY
    assert 'Do not fabricate quotes' not in POLICY
