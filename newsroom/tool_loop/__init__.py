"""
Dual ReAct Tool Execution Engine for Gemini AFC & OpenAI-Compatible Endpoints.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Provides unified interfaces and separate implementations for:
- Google Gemini via Automatic Function Calling (AFC)
- OpenAI-compatible endpoints (DeepSeek, GLM, vLLM, Ollama, OpenAI) via multi-turn chat completions
"""
from newsroom.tool_loop.gemini_loop import run_gemini_tool_loop
from newsroom.tool_loop.models import ToolLoopResult
from newsroom.tool_loop.openai_loop import run_openai_tool_loop
from newsroom.tool_loop.router import execute_agent_tool_loop, resolve_tool_loop_provider
from newsroom.tool_loop.schema import python_func_to_openai_tool

__all__ = [
    "ToolLoopResult",
    "python_func_to_openai_tool",
    "run_openai_tool_loop",
    "run_gemini_tool_loop",
    "execute_agent_tool_loop",
    "resolve_tool_loop_provider",
]
