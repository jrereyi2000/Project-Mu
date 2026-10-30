"""
Tool Loop Router for Multi-Provider Dispatch.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Routes tool loop execution requests to either the Gemini AFC implementation or the
OpenAI-compatible multi-turn implementation based on provider or model.
"""
import os
from typing import Any, Callable, List, Optional

from newsroom.tool_loop.gemini_loop import run_gemini_tool_loop
from newsroom.tool_loop.models import ToolLoopResult
from newsroom.tool_loop.openai_loop import run_openai_tool_loop

OPENAI_COMPATIBLE_PROVIDERS = {
    "openai",
    "ollama",
    "vllm",
    "deepseek",
    "glm",
}


def resolve_tool_loop_provider(provider: Optional[str] = None, model: Optional[str] = None) -> str:
    """Resolve provider name to 'gemini' or one of the OpenAI-compatible providers."""
    if provider:
        p = provider.strip().lower()
        if p == "gemini" or p in OPENAI_COMPATIBLE_PROVIDERS:
            return p
        raise ValueError(
            f"Unsupported tool loop provider: '{provider}'. "
            f"Supported providers: gemini, {', '.join(sorted(OPENAI_COMPATIBLE_PROVIDERS))}"
        )

    if model:
        m = model.strip().lower()
        if m.startswith(("gemini:", "gemini/")) or "gemini" in m:
            return "gemini"
        if m.startswith(("ollama:", "ollama/")):
            return "ollama"
        if m.startswith(("deepseek:", "deepseek/")) or "deepseek" in m:
            return "deepseek"
        if m.startswith(("glm:", "glm/")) or "glm" in m:
            return "glm"
        if m.startswith(("vllm:", "vllm/")):
            return "vllm"
        if m.startswith(("openai:", "openai/")):
            return "openai"
        # Ollama names models "name:tag" (qwen2.5:14b, gemma4:12b); Gemini and
        # hosted model ids never contain a colon.
        if ":" in m:
            return "ollama"

    env_provider = os.environ.get("LLM_PROVIDER")
    if env_provider:
        p = env_provider.strip().lower()
        if p == "gemini" or p in OPENAI_COMPATIBLE_PROVIDERS:
            return p

    return "gemini"


def strip_provider_prefix(model: str, provider: str) -> str:
    """Remove a routing prefix like 'ollama:' or 'deepseek/' that names the resolved provider.

    The prefix only selects the provider; the endpoint expects the bare model id.
    """
    m = (model or "").strip()
    for sep in (":", "/"):
        prefix = provider + sep
        if m.lower().startswith(prefix):
            return m[len(prefix):]
    return m


def execute_agent_tool_loop(
    provider: Optional[str],
    client: Any,
    model: str,
    system_instruction: str,
    prompt: str,
    tools: list[Callable],
    **kwargs: Any,
) -> ToolLoopResult:
    """Execute autonomous tool calling with the specified provider backend.

    Args:
        provider: Provider name ('gemini', 'openai', 'ollama', 'vllm', 'deepseek', 'glm') or None to infer.
        client: Provider client instance (google-genai Client or OpenAI-compatible client) or None.
        model: Target model string.
        system_instruction: System prompt.
        prompt: User task prompt.
        tools: List of tool functions.
        **kwargs: Extra parameters passed to the tool loop (e.g. temperature, max_steps).

    Returns:
        ToolLoopResult from the executed loop.
    """
    resolved_provider = resolve_tool_loop_provider(provider=provider, model=model)
    model = strip_provider_prefix(model, resolved_provider)

    if resolved_provider == "gemini":
        return run_gemini_tool_loop(
            client=client,
            model=model,
            system_instruction=system_instruction,
            prompt=prompt,
            tools=tools,
            **kwargs,
        )
    elif resolved_provider in OPENAI_COMPATIBLE_PROVIDERS:
        return run_openai_tool_loop(
            client=client,
            model=model,
            system_instruction=system_instruction,
            prompt=prompt,
            tools=tools,
            provider=resolved_provider,
            **kwargs,
        )
    else:
        raise ValueError(f"Unhandled provider: {resolved_provider}")
