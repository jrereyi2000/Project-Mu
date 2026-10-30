"""
Tool Schema Converter for OpenAI-Compatible Endpoints.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Converts Python callables, LangChain tools, and Pydantic models into standard
OpenAI function tool definitions:
{
    "type": "function",
    "function": {
        "name": ...,
        "description": ...,
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }
}
"""
import collections.abc
import inspect
import re
import types
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union, get_args, get_origin


def parse_docstring(doc: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Extract general summary and parameter descriptions from a docstring.

    Supports:
    - Google-style (`Args:\n    name (type): description`)
    - Sphinx-style (`:param name: description`)
    - Free-form text
    """
    if not doc:
        return "", {}

    lines = doc.strip().splitlines()
    desc_lines: List[str] = []
    param_docs: Dict[str, str] = {}
    current_param: Optional[str] = None
    in_args_section = False

    section_headers = {
        "args:",
        "arguments:",
        "parameters:",
        "params:",
    }

    for raw_line in lines:
        line = raw_line.strip()
        lower = line.lower()

        # Check section boundaries
        if lower in section_headers:
            in_args_section = True
            current_param = None
            continue

        if in_args_section and line.endswith(":") and not line.startswith(":") and " " not in line.rstrip(":"):
            # New section like Returns:, Examples:, etc.
            in_args_section = False
            current_param = None

        # Sphinx-style :param name: or :param type name:
        if line.startswith(":param") or line.startswith(":arg"):
            in_args_section = False
            m = re.match(r"^:(?:param|arg)(?:\s+[a-zA-Z_0-9]+)?\s+([a-zA-Z_0-9]+)\s*:\s*(.*)$", line)
            if m:
                pname, pdesc = m.group(1), m.group(2).strip()
                param_docs[pname] = pdesc
                current_param = pname
            continue
        elif line.startswith(":return") or line.startswith(":raises"):
            in_args_section = False
            current_param = None
            continue

        if in_args_section:
            # Google style: param_name (type): description OR param_name: description
            m = re.match(r"^([a-zA-Z_0-9]+)(?:\s*\([^)]*\))?\s*:\s*(.*)$", line)
            if m:
                pname, pdesc = m.group(1), m.group(2).strip()
                param_docs[pname] = pdesc
                current_param = pname
            elif current_param and (raw_line.startswith("    ") or raw_line.startswith("\t") or line):
                # Continuation line for current parameter description
                param_docs[current_param] += " " + line
        else:
            if not param_docs:
                desc_lines.append(raw_line)

    general_desc = "\n".join(desc_lines).strip()
    param_docs = {k: v.strip() for k, v in param_docs.items()}
    return general_desc, param_docs


def python_type_to_json_schema(py_type: Any) -> Dict[str, Any]:
    """Map a Python type annotation to an OpenAI-compatible JSON Schema fragment."""
    if py_type is inspect.Parameter.empty or py_type is Any:
        return {"type": "string"}

    # Stringified annotations (e.g. from __future__ import annotations)
    if isinstance(py_type, str):
        t_lower = py_type.strip().lower()
        if t_lower in ("int", "integer"):
            return {"type": "integer"}
        if t_lower in ("float", "number"):
            return {"type": "number"}
        if t_lower in ("bool", "boolean"):
            return {"type": "boolean"}
        if "list" in t_lower:
            return {"type": "array"}
        if "dict" in t_lower:
            return {"type": "object"}
        return {"type": "string"}

    origin = get_origin(py_type)

    # Literal
    if origin is Literal:
        args = get_args(py_type)
        if args:
            first = args[0]
            val_type = "integer" if isinstance(first, int) and not isinstance(first, bool) else (
                "number" if isinstance(first, float) else "string"
            )
            return {"type": val_type, "enum": list(args)}
        return {"type": "string"}

    # Union / Optional (e.g. Union[int, None], int | None)
    if origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = [a for a in get_args(py_type) if a is not type(None)]
        if not args:
            return {"type": "string"}
        if len(args) == 1:
            return python_type_to_json_schema(args[0])
        types_list = []
        for a in args:
            sub = python_type_to_json_schema(a)
            st = sub.get("type")
            if st and st not in types_list:
                types_list.append(st)
        if len(types_list) == 1:
            return {"type": types_list[0]}
        return {"type": types_list}

    # List / Sequence / Iterable
    if origin in (list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.Iterable):
        args = get_args(py_type)
        if args and args[0] is not Any:
            item_schema = python_type_to_json_schema(args[0])
            return {"type": "array", "items": item_schema}
        return {"type": "array"}

    # Dict / Mapping
    if origin in (dict, collections.abc.Mapping):
        return {"type": "object"}

    # Direct standard types
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}
    if py_type is str:
        return {"type": "string"}
    if py_type in (dict, collections.abc.Mapping):
        return {"type": "object"}
    if py_type in (list, tuple, set, collections.abc.Sequence):
        return {"type": "array"}

    # Pydantic BaseModel as a parameter type
    try:
        import pydantic
        if isinstance(py_type, type) and issubclass(py_type, pydantic.BaseModel):
            return py_type.model_json_schema()
    except Exception:
        pass

    return {"type": "string"}


def python_func_to_openai_tool(fn: Any) -> Dict[str, Any]:
    """Convert a Python function, LangChain tool, or Pydantic model into an OpenAI tool schema.

    Returns:
        A dict matching OpenAI function specification:
        {
            "type": "function",
            "function": {
                "name": str,
                "description": str,
                "parameters": {
                    "type": "object",
                    "properties": dict,
                    "required": list[str]
                }
            }
        }
    """
    # 1. Already an OpenAI tool dict
    if isinstance(fn, dict):
        if fn.get("type") == "function" and "function" in fn:
            return fn
        if "name" in fn and "parameters" in fn:
            return {"type": "function", "function": fn}
        return {
            "type": "function",
            "function": {
                "name": fn.get("name", "unknown_tool"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}, "required": []}),
            },
        }

    # 2. Pydantic BaseModel class
    try:
        import pydantic
        if isinstance(fn, type) and issubclass(fn, pydantic.BaseModel):
            model_schema = fn.model_json_schema()
            name = model_schema.get("title", fn.__name__)
            desc = (fn.__doc__ or model_schema.get("description", "")).strip()
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc or f"Execute {name}",
                    "parameters": {
                        "type": "object",
                        "properties": model_schema.get("properties", {}),
                        "required": model_schema.get("required", []),
                    },
                },
            }
    except Exception:
        pass

    # 3. LangChain BaseTool / StructuredTool or object with args_schema
    if hasattr(fn, "args_schema") and getattr(fn, "args_schema") is not None:
        args_schema = getattr(fn, "args_schema")
        try:
            import pydantic
            if isinstance(args_schema, type) and issubclass(args_schema, pydantic.BaseModel):
                model_schema = args_schema.model_json_schema()
                name = getattr(fn, "name", getattr(args_schema, "__name__", "tool"))
                raw_desc = getattr(fn, "description", None) or getattr(args_schema, "__doc__", "") or ""
                desc, param_docs = parse_docstring(raw_desc)
                properties = model_schema.get("properties", {})
                for pname, pdoc in param_docs.items():
                    if pname in properties and "description" not in properties[pname]:
                        properties[pname]["description"] = pdoc
                return {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc or getattr(fn, "description", "") or f"Execute {name}",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": model_schema.get("required", []),
                        },
                    },
                }
        except Exception:
            pass

    # 4. Standard Python callable or wrapped function
    target_fn = getattr(fn, "func", fn)
    name = getattr(fn, "name", None) or getattr(target_fn, "__name__", "tool")
    raw_desc = getattr(fn, "description", None) or getattr(target_fn, "__doc__", "") or ""
    general_desc, param_docs = parse_docstring(raw_desc)

    try:
        sig = inspect.signature(target_fn)
    except (ValueError, TypeError):
        # Uninspectable callable
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": general_desc or f"Execute {name}",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    # Resolve annotations
    try:
        hints = inspect.get_annotations(target_fn, eval_str=True)
    except Exception:
        hints = getattr(target_fn, "__annotations__", {})

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        # Exclude *args and **kwargs
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        param_type = hints.get(param_name, param.annotation)
        p_schema = python_type_to_json_schema(param_type)

        if param_name in param_docs:
            p_schema["description"] = param_docs[param_name]

        properties[param_name] = p_schema

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": general_desc or f"Execute {name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
