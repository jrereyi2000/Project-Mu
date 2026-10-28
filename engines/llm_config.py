"""
LLM Configuration Helper for Thinking Budgets and Generation Configs.
(docs/PRE_PC_WORK.md §5, docs/PC_MIGRATION_PLAN.md §7.1, §7.9)

Parses 'model:level' strings (e.g. 'gemini-3.8-flash:low') and constructs
google.genai.types.GenerateContentConfig with ThinkingConfig.

Supported thinking levels:
- 'low', 'medium', 'high', 'minimal'
- 'default', 'none', 'off', 'unspecified' -> unconstrained / model's default thinking (ThinkingConfig omitted)
"""
import os
from typing import Any, Optional, Tuple

DEFAULT_MODEL = "gemini-3.8-flash"

# Default thinking level for short calls
DEFAULT_SHORT_CALL_THINKING = "low"

LABEL_DEFAULT_THINKING = {
    "agent_memory": "low",
    "scouting_notes": "low",
    "draft_rationale": "low",
    "gm_persona": "low",
    "gm_rationale": "low",
    "gm_negotiation": "low",
}


def parse_model_thinking(
    model_str: Optional[str],
    default_model: str = DEFAULT_MODEL,
    default_thinking: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Parse 'model' or 'model:thinking_level' into (clean_model_name, thinking_level).

    If model_str contains a colon (e.g. 'gemini-3.8-flash:low'), the part after ':' sets the level.
    Levels 'default', 'none', 'off', 'unspecified', or '' disable thinking caps (return None).
    If no colon is present, default_thinking is used (unless it is also 'default'/'none'/etc).
    """
    raw = (model_str or default_model).strip()
    if ":" in raw:
        base_model, _, level_part = raw.partition(":")
        clean_model = base_model.strip()
        lvl = level_part.strip().lower()
        if lvl in ("default", "none", "off", "unspecified", ""):
            return clean_model, None
        return clean_model, lvl
    else:
        clean_model = raw
        if not default_thinking:
            return clean_model, None
        lvl = default_thinking.strip().lower()
        if lvl in ("default", "none", "off", "unspecified", ""):
            return clean_model, None
        return clean_model, lvl


def resolve_thinking_level(
    label: str = "",
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    default: Optional[str] = None,
) -> Optional[str]:
    """Resolve the effective thinking level in priority order:
    1. Explicit ':level' in model string (e.g. 'gemini-3.8-flash:high')
    2. Explicit thinking_level parameter (if not None)
    3. Per-label environment variable (e.g. GM_THINKING_SCOUTING_NOTES, AGENT_MEMORY_THINKING)
    4. General GM/LLM thinking environment variable (GM_LLM_THINKING, LLM_DEFAULT_THINKING)
    5. Label default from LABEL_DEFAULT_THINKING or the default parameter
    """
    # 1. Explicit in model string
    if model and ":" in model:
        _, level = parse_model_thinking(model)
        return level

    # 2. Explicit parameter
    if thinking_level is not None:
        lvl = thinking_level.strip().lower()
        if lvl in ("default", "none", "off", "unspecified", ""):
            return None
        return lvl

    # 3. Label-specific env var
    lbl_clean = (label or "").strip().lower()
    if lbl_clean:
        env_key_specific = f"GM_THINKING_{lbl_clean.upper()}"
        if env_key_specific in os.environ:
            val = os.environ[env_key_specific].strip().lower()
            return None if val in ("default", "none", "off", "unspecified", "") else val

    if "agent_memory" in lbl_clean or label == "agent_memory":
        if "AGENT_MEMORY_THINKING" in os.environ:
            val = os.environ["AGENT_MEMORY_THINKING"].strip().lower()
            return None if val in ("default", "none", "off", "unspecified", "") else val

    # 4. General env vars
    for env_var in ("GM_LLM_THINKING", "LLM_DEFAULT_THINKING"):
        if env_var in os.environ:
            val = os.environ[env_var].strip().lower()
            return None if val in ("default", "none", "off", "unspecified", "") else val

    # 5. Label-based or provided default
    fallback = default if default is not None else LABEL_DEFAULT_THINKING.get(lbl_clean, DEFAULT_SHORT_CALL_THINKING)
    if fallback:
        val = fallback.strip().lower()
        return None if val in ("default", "none", "off", "unspecified", "") else val
    return None


def get_thinking_config(thinking_level: Optional[str]):
    """Construct a google.genai.types.ThinkingConfig or return None."""
    if not thinking_level:
        return None
    from google.genai import types
    return types.ThinkingConfig(thinking_level=thinking_level.upper())


def make_content_config(
    model: Optional[str] = None,
    default_thinking: Optional[str] = None,
    thinking_level: Optional[str] = None,
    temperature: Optional[float] = None,
    system_instruction: Optional[str] = None,
    response_mime_type: Optional[str] = None,
    response_schema: Any = None,
    **extra,
) -> Tuple[Any, str]:
    """Constructs a google.genai.types.GenerateContentConfig and returns (config, clean_model).

    Handles thinking_config construction from model:level or resolved thinking_level.
    """
    from google.genai import types

    clean_model, parsed_level = parse_model_thinking(model, default_thinking=default_thinking)
    effective_level = thinking_level if thinking_level is not None else parsed_level

    config_kwargs: dict[str, Any] = {}
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if system_instruction is not None:
        config_kwargs["system_instruction"] = system_instruction
    if response_mime_type is not None:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    config_kwargs.update(extra)

    if "thinking_config" not in config_kwargs:
        tc = get_thinking_config(effective_level)
        if tc is not None:
            config_kwargs["thinking_config"] = tc

    return types.GenerateContentConfig(**config_kwargs), clean_model
