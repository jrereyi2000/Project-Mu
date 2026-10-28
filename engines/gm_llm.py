"""
The Gemini seam for the GM agents (docs/done/team_management/03_GM_AGENTS_PLAN.md §2.8; PROJECT_SPEC §3.3f).

Everything GM-related that may use an LLM (persona bios at seeding, trade rationale) takes an `llm` argument:
a callable prompt -> text, or None for the template fallback. make_llm() builds the live one only when a key
is configured, metered through db.llm_usage.UsageMeter under the given label; tests pass a fake callable and
never reach Gemini. The LLM never chooses or changes a deal.
"""
import os

DEFAULT_MODEL = "gemini-3.8-flash"     # the newsroom's default (newsroom/weekly_run.py)


def api_key():
    from pipeline.notify import env
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or env("GEMINI_API_KEY")


class GeminiText:
    """prompt -> text through one genai client, every call counted by a UsageMeter. flush() writes the counted
    calls to llm_usage under `label` (never raises)."""

    def __init__(self, key, label, model=DEFAULT_MODEL, temperature=0.7, thinking_level=None, client=None):
        from google import genai
        from db.llm_usage import UsageMeter
        from engines.llm_config import make_content_config, resolve_thinking_level

        self.label = label
        self.model = model
        self.temperature = temperature
        self.thinking_level = resolve_thinking_level(
            label=label,
            model=model,
            thinking_level=thinking_level,
            default="low",
        )
        self.config, self.model_name = make_content_config(
            model=model,
            thinking_level=self.thinking_level,
            temperature=temperature,
        )
        self.client = client if client is not None else genai.Client(api_key=key)
        self.meter = UsageMeter(self.client)

    def __call__(self, prompt):
        with self.meter.labelled(self.label):
            resp = self.client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=self.config)
        return (resp.text or "").strip()

    def flush(self):
        try:
            return self.meter.record(self.label, label=self.label, model=self.model_name)
        except Exception as e:                                   # logging never breaks a run
            print(f"[gm_llm] usage not recorded: {e}", flush=True)
            return None


def make_llm(label, model=DEFAULT_MODEL, enabled=True, thinking_level=None, client=None,
             provider=None, fallback_model=None):
    """The live prompt -> text callable, or None (template fallback) when disabled, without a key, or when the
    provider is unavailable."""
    if not enabled:
        return None
    from engines.llm_provider import resolve_provider, ProviderText
    prov = resolve_provider(label=label, model=model, provider=provider)
    key = api_key()
    if prov == "gemini" and not key and client is None:
        return None
    try:
        if prov == "ollama":
            return ProviderText(
                label=label,
                source=label,
                model=model,
                provider="ollama",
                fallback_model=fallback_model,
                thinking_level=thinking_level,
                client=client,
                api_key=key,
            )
        return GeminiText(key, label, model, thinking_level=thinking_level, client=client)
    except Exception as e:
        print(f"[gm_llm] LLM unavailable, using templates: {e}", flush=True)
        return None


def call(llm, prompt):
    """llm(prompt), or None when there's no llm or the call fails (the caller falls back to its template)."""
    if llm is None:
        return None
    try:
        return llm(prompt)
    except Exception as e:
        print(f"[gm_llm] call failed, using the template: {e}", flush=True)
        return None


def flush(llm):
    if llm is not None and hasattr(llm, "flush"):
        llm.flush()
