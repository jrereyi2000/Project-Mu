"""
OpenAI-Compatible Multi-Turn Tool Loop.
(docs/done/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Executes multi-turn tool calling with OpenAI-compatible endpoints (DeepSeek, GLM,
vLLM, Ollama, OpenAI) using standard chat completions API:
client.chat.completions.create(model=..., messages=..., tools=...).
"""
import collections
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from newsroom.tool_loop.models import ToolLoopResult
from newsroom.tool_loop.schema import python_func_to_openai_tool


def extract_openai_usage(usage_obj: Any) -> Dict[str, int]:
    """Extract token counts (prompt, cached/miss, completion, reasoning) from OpenAI usage.

    DeepSeek reports ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` and
    ``completion_tokens_details.reasoning_tokens``; its schema nests the cache fields
    under ``prompt_tokens_details`` but its example responses put them at the top
    level, so both spellings are accepted. ``uncached_tokens`` is the cache-miss
    count when the provider reports it, otherwise prompt tokens minus cached tokens.
    """
    if not usage_obj:
        return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                "uncached_tokens": 0, "thinking_tokens": 0}

    if isinstance(usage_obj, dict):
        p_tokens = usage_obj.get("prompt_tokens") or usage_obj.get("input_tokens") or 0
        c_tokens = usage_obj.get("completion_tokens") or usage_obj.get("output_tokens") or 0
        p_details = usage_obj.get("prompt_tokens_details") or {}
        c_details = usage_obj.get("completion_tokens_details") or {}
        cached = (p_details.get("cached_tokens") or p_details.get("prompt_cache_hit_tokens")
                  or usage_obj.get("prompt_cache_hit_tokens") or usage_obj.get("cached_tokens") or 0)
        uncached = (p_details.get("prompt_cache_miss_tokens")
                    or usage_obj.get("prompt_cache_miss_tokens") or 0)
        thinking = (c_details.get("reasoning_tokens") or usage_obj.get("reasoning_tokens")
                    or usage_obj.get("thinking_tokens") or 0)
    else:
        # Attribute access on ChatCompletion.usage object
        p_tokens = getattr(usage_obj, "prompt_tokens", 0) or getattr(usage_obj, "input_tokens", 0) or 0
        c_tokens = getattr(usage_obj, "completion_tokens", 0) or getattr(usage_obj, "output_tokens", 0) or 0
        p_details = getattr(usage_obj, "prompt_tokens_details", None)
        c_details = getattr(usage_obj, "completion_tokens_details", None)
        cached = (getattr(p_details, "cached_tokens", 0) or getattr(p_details, "prompt_cache_hit_tokens", 0)
                  if p_details else 0) or getattr(usage_obj, "prompt_cache_hit_tokens", 0) or getattr(usage_obj, "cached_tokens", 0) or 0
        uncached = (getattr(p_details, "prompt_cache_miss_tokens", 0) if p_details else 0) \
            or getattr(usage_obj, "prompt_cache_miss_tokens", 0) or 0
        thinking = (getattr(c_details, "reasoning_tokens", 0) if c_details else 0) \
            or getattr(usage_obj, "reasoning_tokens", 0) or getattr(usage_obj, "thinking_tokens", 0) or 0

    p_tokens, c_tokens = int(p_tokens), int(c_tokens)
    cached = min(int(cached), p_tokens)
    uncached = int(uncached) if uncached else max(p_tokens - cached, 0)
    return {
        "input_tokens": p_tokens,
        "output_tokens": c_tokens,
        "cached_tokens": cached,
        "uncached_tokens": uncached,
        "thinking_tokens": int(thinking),
    }


# Default endpoints used when no client is passed. Each base URL can be
# overridden by its env var; hosted providers need their API key set.
#   provider: (base_url env, default base_url, api key env or None for local)
PROVIDER_ENDPOINTS: Dict[str, tuple] = {
    "openai": ("OPENAI_BASE_URL", None, "OPENAI_API_KEY"),
    "deepseek": ("DEEPSEEK_BASE_URL", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "glm": ("GLM_BASE_URL", "https://api.z.ai/api/paas/v4", "GLM_API_KEY"),
    "vllm": ("VLLM_BASE_URL", "http://localhost:8000/v1", None),
    "ollama": ("OLLAMA_OPENAI_BASE_URL", None, None),
}


def _env(name: str) -> Optional[str]:
    val = os.environ.get(name)
    if val:
        return val
    try:
        from pipeline.notify import env
        return env(name) or None
    except Exception:
        return None


def resolve_endpoint(provider: str) -> tuple:
    """Return (base_url, api_key) for a provider's default endpoint.

    base_url None means the OpenAI SDK default. Raises ValueError for an
    unknown provider or a hosted provider with no API key.
    """
    if provider not in PROVIDER_ENDPOINTS:
        raise ValueError(f"No default endpoint for provider '{provider}'")
    url_env, default_url, key_env = PROVIDER_ENDPOINTS[provider]

    base_url = _env(url_env) or default_url
    if provider == "ollama" and not base_url:
        from engines.llm_provider import get_ollama_base_url
        base_url = get_ollama_base_url().rstrip("/") + "/v1"

    if key_env:
        api_key = _env(key_env)
        if not api_key:
            raise ValueError(f"{key_env} is not set")
    else:
        # Local servers ignore the key, but the SDK requires a non-empty one.
        api_key = "local"
    return base_url, api_key


def build_default_client(provider: str, max_retries: Optional[int] = None) -> Any:
    """Create an OpenAI SDK client pointed at the given provider's endpoint.

    ``max_retries=None`` keeps the SDK default; pass ``0`` for a single attempt,
    matching the pilot's Gemini retry policy.
    """
    base_url, api_key = resolve_endpoint(provider)
    import openai
    kwargs: Dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return openai.OpenAI(**kwargs)


def run_openai_tool_loop(
    client: Any,
    model: str,
    system_instruction: str,
    prompt: str,
    tools: list[Callable],
    max_steps: int = 50,
    temperature: float = 0.7,
    provider: str = "openai",
    max_tokens: Optional[int] = None,
) -> ToolLoopResult:
    """Execute multi-turn autonomous tool calling loop against an OpenAI-compatible endpoint.

    Args:
        client: An OpenAI or OpenAI-compatible client (with client.chat.completions.create).
        model: Target model identifier (e.g. 'deepseek-chat', 'glm-4', 'qwen2.5:14b').
        system_instruction: System prompt framing persona, directives, and constraints.
        prompt: User task prompt.
        tools: List of Python callables, LangChain tools, or schema dicts available to model.
        max_steps: Maximum loop iterations before terminating.
        temperature: Sampling temperature for generation.
        provider: Which endpoint to build a client for when client is None
            ('openai', 'deepseek', 'glm', 'vllm', 'ollama'); see PROVIDER_ENDPOINTS.
        max_tokens: Optional cap on generated tokens, sent on every request,
            including the fallback synthesis call.

    Returns:
        ToolLoopResult with generated text, token usage, tool counts, and iterations.
    """
    if client is None:
        try:
            client = build_default_client(provider)
        except Exception as e:
            return ToolLoopResult(
                text="",
                error=f"No client provided and failed to create a default {provider} client: {e}",
            )

    # Prepare tool mapping and schemas
    tools_dict: Dict[str, Callable] = {}
    openai_tools: List[Dict[str, Any]] = []

    for t in (tools or []):
        schema = python_func_to_openai_tool(t)
        fn_name = schema["function"]["name"]
        callable_fn = getattr(t, "func", t)
        tools_dict[fn_name] = callable_fn
        openai_tools.append(schema)

    # Initialize messages
    messages: List[Dict[str, Any]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    iterations = 0
    tool_calls_count: Dict[str, int] = collections.defaultdict(int)
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                   "uncached_tokens": 0, "thinking_tokens": 0}
    usage_calls: List[Dict[str, Any]] = []
    final_text = ""
    failure_error: Optional[str] = None

    while iterations < max_steps:
        iterations += 1

        call_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if openai_tools:
            call_kwargs["tools"] = openai_tools
            call_kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        try:
            call_started = time.perf_counter()
            response = client.chat.completions.create(**call_kwargs)
            call_latency = time.perf_counter() - call_started
        except Exception as ex:
            print(f"\n❌ [OPENAI TOOL LOOP API ERROR]: {ex}")
            failure_error = str(ex)
            return ToolLoopResult(
                text=f"Error calling OpenAI API: {ex}",
                usage=total_usage,
                tool_calls_count=dict(tool_calls_count),
                iterations=iterations,
                error=failure_error,
                usage_calls=usage_calls,
            )

        # Accumulate usage tokens, retaining each request separately for per-call
        # accounting (uncached/cached input, output and reasoning stay distinct).
        usage_data = getattr(response, "usage", None)
        if usage_data:
            step_usage = extract_openai_usage(usage_data)
            for k, v in step_usage.items():
                total_usage[k] += v
            usage_calls.append(dict(step_usage, latency_s=round(call_latency, 3)))

        if not hasattr(response, "choices") or not response.choices:
            failure_error = "Model returned empty choices."
            break

        choice = response.choices[0]
        message = choice.message

        # Check for tool calls
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls and isinstance(message, dict):
            tool_calls = message.get("tool_calls")

        if tool_calls:
            # Build assistant message carrying tool calls
            assistant_content = getattr(message, "content", None)
            if assistant_content is None and isinstance(message, dict):
                assistant_content = message.get("content")

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [],
            }

            # DeepSeek thinking mode requires the previous turns' reasoning_content
            # to be sent back whenever tools are offered, or the API returns 400.
            # Other providers do not return the field, so it is omitted for them.
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning is None and isinstance(message, dict):
                reasoning = message.get("reasoning_content")
            if reasoning is not None:
                assistant_msg["reasoning_content"] = reasoning

            for tc in tool_calls:
                tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else f"call_{sum(tool_calls_count.values())}")
                if hasattr(tc, "function"):
                    fn_obj = tc.function
                    fn_name = getattr(fn_obj, "name", "")
                    fn_args = getattr(fn_obj, "arguments", "{}")
                elif isinstance(tc, dict) and "function" in tc:
                    fn_name = tc["function"].get("name", "")
                    fn_args = tc["function"].get("arguments", "{}")
                else:
                    fn_name = getattr(tc, "name", "")
                    fn_args = getattr(tc, "arguments", "{}")

                assistant_msg["tool_calls"].append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": fn_args if isinstance(fn_args, str) else json.dumps(fn_args),
                    },
                })
            messages.append(assistant_msg)

            # Execute tool calls
            for tc_item in assistant_msg["tool_calls"]:
                tc_id = tc_item["id"]
                fn_name = tc_item["function"]["name"]
                fn_args_raw = tc_item["function"]["arguments"]

                # Parse JSON arguments
                if isinstance(fn_args_raw, str):
                    try:
                        fn_kwargs = json.loads(fn_args_raw) if fn_args_raw.strip() else {}
                    except Exception as parse_ex:
                        err_msg = f"Tool Error: Failed to parse arguments as JSON: {parse_ex}"
                        print(f"\n⚙️  [DATABASE TOOL ERROR: JSON PARSE] {fn_name}: {parse_ex}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": err_msg,
                        })
                        continue
                elif isinstance(fn_args_raw, dict):
                    fn_kwargs = fn_args_raw
                else:
                    fn_kwargs = {}

                # Check if tool is recognized
                if fn_name not in tools_dict:
                    err_msg = f"Tool Error: Tool '{fn_name}' is not recognized."
                    print(f"\n⚙️  [DATABASE TOOL NOT FOUND] {fn_name}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": fn_name,
                        "content": err_msg,
                    })
                    continue

                tool_calls_count[fn_name] += 1
                call_idx = sum(tool_calls_count.values())
                call_repr = f"{fn_name}({', '.join([f'{k}={v!r}' for k, v in fn_kwargs.items()])})"
                print(f"\n⚙️  [DATABASE TOOL #{call_idx} INVOKED] {call_repr}")

                # Call tool with robust exception handling
                try:
                    tool_fn = tools_dict[fn_name]
                    res = tool_fn(**fn_kwargs)
                    output_str = str(res)
                    preview = output_str[:160] + "..." if len(output_str) > 160 else output_str
                    print(f"   -> Result preview: {preview}")
                except Exception as exec_ex:
                    print(f"   -> Tool Error: {exec_ex}")
                    output_str = f"Tool Error: {exec_ex}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": fn_name,
                    "content": output_str,
                })
        else:
            # Model returned final assistant message (no further tool calls)
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            final_text = content or ""
            break

    # If maximum steps reached without clean exit
    if iterations >= max_steps and not final_text:
        failure_error = f"Maximum steps ({max_steps}) reached before completion."

    # Parity fallback: if research occurred but no final content was produced
    if not (final_text or "").strip() and failure_error is None and sum(tool_calls_count.values()) > 0:
        print("\n⚠️ [TOOL TURN COMPLETED WITHOUT FINAL TEXT] Prompting model to synthesize research into final output...")
        messages.append({
            "role": "user",
            "content": "Now, using the database research and data you gathered, write your complete final article following your assigned article structure and formatting directives. Do not execute any further tool calls.",
        })
        try:
            followup_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens is not None:
                followup_kwargs["max_tokens"] = max_tokens
            follow_started = time.perf_counter()
            followup = client.chat.completions.create(**followup_kwargs)
            follow_latency = time.perf_counter() - follow_started
            if getattr(followup, "usage", None):
                follow_usage = extract_openai_usage(followup.usage)
                for k, v in follow_usage.items():
                    total_usage[k] += v
                usage_calls.append(dict(follow_usage, latency_s=round(follow_latency, 3)))
            if hasattr(followup, "choices") and followup.choices:
                final_text = getattr(followup.choices[0].message, "content", "") or ""
        except Exception as syn_ex:
            print(f"⚠️ [SYNTHESIS FALLBACK FAILED]: {syn_ex}")

    # Summary reporting
    total_tool_calls = sum(tool_calls_count.values())
    print("\n📊 --- RESEARCH TOOL USAGE SUMMARY ---")
    print(f"Total Database Tool Calls: {total_tool_calls} (Cap: {max_steps})")
    if tool_calls_count:
        for tool_name, count in sorted(tool_calls_count.items(), key=lambda x: x[1], reverse=True):
            print(f"   • {tool_name}: {count} call{'s' if count != 1 else ''}")
    else:
        print("   • No database tools invoked (used Assignment Brief context directly).")
    print("---------------------------------------\n")

    return ToolLoopResult(
        text=final_text,
        usage=total_usage,
        tool_calls_count=dict(tool_calls_count),
        iterations=iterations,
        error=failure_error,
        usage_calls=usage_calls,
    )
