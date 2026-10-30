"""
Models and Dataclasses for the Newsroom Tool Loops.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)
"""
from dataclasses import dataclass, field
from typing import Dict, Optional, Any


@dataclass
class ToolLoopResult:
    """Standardized result returned by both Gemini AFC and OpenAI-compatible tool loops.

    Attributes:
        text: The final generated response text from the model.
        usage: Token usage breakdown (input_tokens, output_tokens, cached_tokens, thinking_tokens).
        tool_calls_count: Mapping of tool function names to invocation counts.
        iterations: Number of turns / loop iterations executed.
        error: Optional error string if execution failed or terminated abnormally.
    """
    text: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    tool_calls_count: Dict[str, int] = field(default_factory=dict)
    iterations: int = 0
    error: Optional[str] = None

    @property
    def total_tool_calls(self) -> int:
        """Total number of tool calls executed across all tools."""
        return sum(self.tool_calls_count.values())

    @property
    def is_success(self) -> bool:
        """True if execution produced text without fatal error."""
        return bool(self.text.strip()) and self.error is None
