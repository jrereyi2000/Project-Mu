# Newsroom Tool Loop: Dual ReAct Execution Engine

**Reference:** `docs/in_progress/PC_MIGRATION_PLAN.md` §7.3, §7.4; `docs/not_started/PRE_PC_WORK_2.md` Item A6.

---

## 1. Overview

The `newsroom.tool_loop` package provides an autonomous ReAct tool execution engine supporting both:
1. **Google Gemini (AFC)**: Uses Google GenAI SDK's native `AutomaticFunctionCallingConfig` to execute database research tools with minimal latency overhead.
2. **OpenAI-Compatible Endpoints**: Multi-turn ReAct execution loop designed for open-weights and hosted models (e.g. DeepSeek V4.1 Flash, GLM-5.3-Flash, vLLM, Ollama `qwen2.5:14b`, OpenAI) using standard `chat.completions.create(..., tools=...)`.

This dual architecture ensures that existing Gemini newsroom workflows remain 100% intact while enabling side-by-side quality evaluations and migration to hosted/local open models without breaking existing tool signatures.

---

## 2. Package Architecture

```
newsroom/tool_loop/
├── __init__.py           # Unified exports (ToolLoopResult, runners, router, schema converter)
├── models.py             # ToolLoopResult dataclass (text, usage, tool_calls_count, iterations, error)
├── schema.py             # python_func_to_openai_tool: schema inspection and converter
├── openai_loop.py        # run_openai_tool_loop: multi-turn loop with JSON parsing & error recovery
├── gemini_loop.py        # run_gemini_tool_loop: encapsulated Gemini AFC loop with fallback synthesis
├── router.py             # execute_agent_tool_loop: provider dispatcher
└── README.md             # This documentation
```

---

## 3. Key Components

### 3.1 Tool Schema Converter (`schema.py`)
`python_func_to_openai_tool(fn)` inspects callables and generates standard OpenAI tool definitions:
- **Type Annotations**: Maps Python primitives (`int`, `float`, `str`, `bool`), collections (`list[str]`, `dict`), `Literal`, and `Optional` / `Union` types to JSON Schema.
- **Docstrings**: Parses Google-style (`Args:`) and Sphinx-style (`:param name:`) docstrings to populate tool descriptions and individual parameter descriptions.
- **LangChain & Pydantic**: Directly extracts schemas from LangChain `@tool` decorators, `BaseTool.args_schema`, or standalone Pydantic `BaseModel` classes.
- **Parameter Requirements**: Correctly marks parameters without defaults as required, while omitting `*args` and `**kwargs`.

### 3.2 OpenAI Tool Loop (`openai_loop.py`)
`run_openai_tool_loop(client, model, system_instruction, prompt, tools, max_steps=50, temperature=0.7)`:
- Formats messages with `system` and `user` roles.
- Invokes `client.chat.completions.create(model=..., messages=..., tools=..., tool_choice="auto")`.
- When tool calls are returned:
  - Appends assistant message carrying tool calls.
  - Safely parses JSON arguments (returning helpful parse error messages to model if malformed).
  - Executes target Python callable with formatted invocation logging.
  - Appends tool output messages (`role: "tool"`).
  - Repeats until model returns final text or `max_steps` is reached.
- **Fallback Synthesis**: If tools were invoked but the model completed the turn without synthesized markdown, prompts model for a final synthesis pass (matching Gemini AFC behavior).
- **Token Usage**: Extracts prompt, completion, cached, and reasoning/thinking tokens across all turns.

### 3.3 Gemini Tool Loop (`gemini_loop.py`)
`run_gemini_tool_loop(client, model, system_instruction, prompt, tools, temperature=0.7, max_steps=50)`:
- Encapsulates lines 1494–1540 of `newsroom/swarm_dispatcher.py`.
- Wraps tools with `@functools.wraps` to preserve signatures while providing step-by-step console logging.
- Configures `types.AutomaticFunctionCallingConfig(maximum_remote_calls=max_steps)`.
- Handles candidate chunk assembly and fallback synthesis prompts.

### 3.4 Router (`router.py`)
`execute_agent_tool_loop(provider, client, model, system_instruction, prompt, tools, **kwargs)`:
- Dispatches to `run_gemini_tool_loop` if `provider == "gemini"`.
- Dispatches to `run_openai_tool_loop` if `provider in ("openai", "ollama", "vllm", "deepseek", "glm")`.
- If `provider` is omitted, infers provider from the `model` name prefix (e.g. `ollama:`, `deepseek:`) or `LLM_PROVIDER` environment variable.

---

## 4. Usage Example

```python
from newsroom.tool_loop import execute_agent_tool_loop

def get_player_stats(name: str, season: int = 2026) -> str:
    """Fetch season passing/rushing stats for a player.
    
    Args:
        name: Player's full name.
        season: NFL season year.
    """
    return f"{name} ({season}): 4,120 Pass Yds, 34 TD, 8 INT"

# Dispatch through router
result = execute_agent_tool_loop(
    provider="deepseek",
    client=openai_client,
    model="deepseek-chat",
    system_instruction="You are an award-winning beat writer.",
    prompt="Write an assessment of the starting QB.",
    tools=[get_player_stats],
    temperature=0.7,
    max_steps=20,
)

print(result.text)
print("Tool calls:", result.tool_calls_count)
print("Token usage:", result.usage)
```
