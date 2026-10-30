"""
Gemini AFC Tool Loop Encapsulation.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Encapsulates the Gemini Automatic Function Calling (AFC) pattern used in
newsroom/swarm_dispatcher.py, preserving exact tool logging, parameter signature
preservation, and fallback synthesis.
"""
import collections
import functools
import os
from typing import Any, Callable, Dict, List, Optional

from newsroom.tool_loop.models import ToolLoopResult


def run_gemini_tool_loop(
    client: Any,
    model: str,
    system_instruction: str,
    prompt: str,
    tools: list[Callable],
    temperature: float = 0.7,
    max_steps: int = 50,
) -> ToolLoopResult:
    """Execute Gemini Automatic Function Calling (AFC) tool loop.

    Args:
        client: A google-genai Client instance or None (resolved from GEMINI_API_KEY).
        model: Target Gemini model identifier (e.g. 'gemini-3.8-flash').
        system_instruction: System prompt framing persona, directives, and constraints.
        prompt: User task prompt.
        tools: List of Python callables or LangChain tools available to the model.
        temperature: Sampling temperature for generation.
        max_steps: Maximum remote tool calls permitted by AFC config.

    Returns:
        ToolLoopResult with generated text, token usage, tool counts, and iterations.
    """
    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            try:
                from pipeline.notify import env
                api_key = env("GEMINI_API_KEY")
            except Exception:
                api_key = None
        if not api_key:
            print("\n⚠️  [GEMINI LIVE CALL SKIPPED]: GEMINI_API_KEY environment variable is not set.")
            return ToolLoopResult(
                text="Gemini execution skipped: Missing GEMINI_API_KEY.",
                error="missing GEMINI_API_KEY",
            )
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
        except Exception as e:
            return ToolLoopResult(
                text=f"Error initializing Gemini client: {e}",
                error=str(e),
            )

    from google.genai import types

    # Prepare raw functions with logging wrappers for Gemini AFC (preserving parameter signatures)
    tool_call_counts: Dict[str, int] = collections.defaultdict(int)
    tools_for_gemini: List[Callable] = []

    for t in (tools or []):
        fn = getattr(t, "func", t)

        def make_wrapper(inner_fn):
            @functools.wraps(inner_fn)
            def tool_wrapper(*args, **kwargs):
                tool_call_counts[inner_fn.__name__] += 1
                call_idx = sum(tool_call_counts.values())
                call_repr = f"{inner_fn.__name__}({', '.join([str(a) for a in args] + [f'{k}={v}' for k, v in kwargs.items()])})"
                print(f"\n⚙️  [DATABASE TOOL #{call_idx} INVOKED] {call_repr}")
                try:
                    res = inner_fn(*args, **kwargs)
                    preview = str(res)[:160] + "..." if len(str(res)) > 160 else str(res)
                    print(f"   -> Result preview: {preview}")
                    return str(res)
                except Exception as ex:
                    print(f"   -> Tool Error: {ex}")
                    return f"Tool Error: {ex}"

            return tool_wrapper

        tools_for_gemini.append(make_wrapper(fn))

    iterations = 1
    generated_text = ""
    failure_error: Optional[str] = None
    response = None
    followup = None

    try:
        chat = client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_for_gemini,
                temperature=temperature,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=max_steps
                ),
            ),
        )

        response = chat.send_message(prompt)
        generated_text = getattr(response, "text", "") or ""

        if not generated_text and hasattr(response, "candidates") and response.candidates:
            parts = response.candidates[0].content.parts
            text_chunks = [p.text for p in parts if hasattr(p, "text") and p.text]
            if text_chunks:
                generated_text = "\n".join(text_chunks)

        # Fallback if AFC cycle completed without final synthesized markdown text
        if not generated_text:
            print("\n⚠️ [AFC TURN COMPLETED WITHOUT FINAL TEXT] Prompting model to synthesize research into final output...")
            iterations += 1
            followup = chat.send_message(
                "Now, using the database research and data you gathered, write your complete final article following your assigned article structure and formatting directives. Do not execute any further tool calls."
            )
            generated_text = getattr(followup, "text", "") or ""
            if not generated_text and hasattr(followup, "candidates") and followup.candidates:
                parts = followup.candidates[0].content.parts
                text_chunks = [p.text for p in parts if hasattr(p, "text") and p.text]
                if text_chunks:
                    generated_text = "\n".join(text_chunks)

    except Exception as e:
        print(f"\n❌ Error calling Gemini: {e}")
        failure_error = str(e)
        return ToolLoopResult(
            text=f"Error calling Gemini: {e}",
            tool_calls_count=dict(tool_call_counts),
            iterations=iterations,
            error=failure_error,
        )

    # Extract usage
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "thinking_tokens": 0}
    meter = getattr(client, "_usage_meter", None)
    calls = getattr(meter, "calls", None)
    if isinstance(calls, list) and len(calls) > 0:
        usage["input_tokens"] = sum(int(c.get("input_tokens", 0)) for c in calls)
        usage["output_tokens"] = sum(int(c.get("output_tokens", 0)) for c in calls)
        usage["cached_tokens"] = sum(int(c.get("cached_tokens", 0)) for c in calls)
        usage["thinking_tokens"] = sum(int(c.get("thinking_tokens", 0)) for c in calls)
    else:
        # Without the meter, only the responses we hold are visible: the AFC
        # turn's final response and the synthesis follow-up. Sum both.
        # (Intermediate AFC calls are only counted by the meter.)
        for resp in (response, followup):
            u = getattr(resp, "usage_metadata", None) if resp is not None else None
            if u is not None:
                usage["input_tokens"] += int(getattr(u, "prompt_token_count", 0) or 0)
                usage["output_tokens"] += int(getattr(u, "candidates_token_count", 0) or 0)
                usage["cached_tokens"] += int(getattr(u, "cached_content_token_count", 0) or 0)
                usage["thinking_tokens"] += int(getattr(u, "thoughts_token_count", 0) or 0)

    total_tool_calls = sum(tool_call_counts.values())
    print("\n📊 --- RESEARCH TOOL USAGE SUMMARY ---")
    print(f"Total Database Tool Calls: {total_tool_calls} (Cap: {max_steps})")
    if tool_call_counts:
        for tool_name, count in sorted(tool_call_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"   • {tool_name}: {count} call{'s' if count != 1 else ''}")
    else:
        print("   • No database tools invoked (used Assignment Brief context directly).")
    print("---------------------------------------\n")

    return ToolLoopResult(
        text=generated_text or "",
        usage=usage,
        tool_calls_count=dict(tool_call_counts),
        iterations=iterations,
        error=failure_error,
    )
