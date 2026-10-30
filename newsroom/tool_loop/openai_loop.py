"""
OpenAI-Compatible Multi-Turn Tool Loop.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Executes multi-turn tool calling with OpenAI-compatible endpoints (DeepSeek, GLM,
vLLM, Ollama, OpenAI) using standard chat completions API:
client.chat.completions.create(model=..., messages=..., tools=...).
"""
import collections
import json
import os
from typing import Any, Callable, Dict, List, Optional

from newsroom.tool_loop.models import ToolLoopResult
from newsroom.tool_loop.schema import python_func_to_openai_tool


def extract_openai_usage(usage_obj: Any) -> Dict[str, int]:
    """Extract token counts (prompt, completion, cached, reasoning) from OpenAI usage."""
    if not usage_obj:
        return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "thinking_tokens": 0}

    if isinstance(usage_obj, dict):
        p_tokens = usage_obj.get("prompt_tokens") or usage_obj.get("input_tokens") or 0
        c_tokens = usage_obj.get("completion_tokens") or usage_obj.get("output_tokens") or 0
        p_details = usage_obj.get("prompt_tokens_details") or {}
        c_details = usage_obj.get("completion_tokens_details") or {}
        cached = p_details.get("cached_tokens") or usage_obj.get("cached_tokens") or 0
        thinking = c_details.get("reasoning_tokens") or usage_obj.get("thinking_tokens") or 0
        return {
            "input_tokens": int(p_tokens),
            "output_tokens": int(c_tokens),
            "cached_tokens": int(cached),
            "thinking_tokens": int(thinking),
        }

    # Attribute access on ChatCompletion.usage object
    p_tokens = getattr(usage_obj, "prompt_tokens", 0) or getattr(usage_obj, "input_tokens", 0) or 0
    c_tokens = getattr(usage_obj, "completion_tokens", 0) or getattr(usage_obj, "output_tokens", 0) or 0
    p_details = getattr(usage_obj, "prompt_tokens_details", None)
    c_details = getattr(usage_obj, "completion_tokens_details", None)
    cached = getattr(p_details, "cached_tokens", 0) if p_details else getattr(usage_obj, "cached_tokens", 0) or 0
    thinking = getattr(c_details, "reasoning_tokens", 0) if c_details else getattr(usage_obj, "thinking_tokens", 0) or 0

    return {
        "input_tokens": int(p_tokens),
        "output_tokens": int(c_tokens),
        "cached_tokens": int(cached),
        "thinking_tokens": int(thinking),
    }


def run_openai_tool_loop(
    client: Any,
    model: str,
    system_instruction: str,
    prompt: str,
    tools: list[Callable],
    max_steps: int = 50,
    temperature: float = 0.7,
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

    Returns:
        ToolLoopResult with generated text, token usage, tool counts, and iterations.
    """
    if client is None:
        try:
            import openai
            api_key = os.environ.get("OPENAI_API_KEY", "dummy-key")
            client = openai.OpenAI(api_key=api_key)
        except Exception as e:
            return ToolLoopResult(
                text="",
                error=f"No OpenAI client provided and failed to instantiate default: {e}",
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
    total_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "thinking_tokens": 0}
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

        try:
            response = client.chat.completions.create(**call_kwargs)
        except Exception as ex:
            print(f"\n❌ [OPENAI TOOL LOOP API ERROR]: {ex}")
            failure_error = str(ex)
            return ToolLoopResult(
                text=f"Error calling OpenAI API: {ex}",
                usage=total_usage,
                tool_calls_count=dict(tool_calls_count),
                iterations=iterations,
                error=failure_error,
            )

        # Accumulate usage tokens
        usage_data = getattr(response, "usage", None)
        if usage_data:
            step_usage = extract_openai_usage(usage_data)
            for k, v in step_usage.items():
                total_usage[k] += v

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
            followup = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            if getattr(followup, "usage", None):
                for k, v in extract_openai_usage(followup.usage).items():
                    total_usage[k] += v
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
    )
