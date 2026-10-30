"""Mod recipes: one JSON file per mod, data/mod_builds/<slug>/recipe.json (MOD_BUILDER_PLAN §2.1).

    {"slug": "yellow_first_down", "title": "...", "author": "JR (Project Mu)", "version": "1.0",
     "category": "Cosmetic", "description": "...", "route": "harness",
     "ops": [{"op": "texture-recolor", "asset": "<texture>", "recolor": {"mode": "tint", "color": "#FFD400"}},
             {"op": "texture-replace", "asset": "<texture>", "file": "work/new.png"},
             {"op": "ebx-set", "asset": "<ebx>", "path": "<field path>", "value": "#FF69B4"},
             {"op": "legacy-replace", "path": "<legacy file path>", "file": "work/file.json"}]}

`route` is "harness" or "gui"; an op may carry its own `route`. Relative `file` paths are relative to the
recipe's folder. `validate` returns a list of problems (empty = valid).
"""
import json
import os
import re
from dataclasses import dataclass, field

OP_KINDS = ("texture-recolor", "texture-replace", "ebx-set", "legacy-replace")
ROUTES = ("harness", "gui")
RECOLOR_MODES = ("tint", "hue-shift", "map")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
IMAGE_EXTS = (".png", ".dds")


@dataclass
class Op:
    kind: str
    asset: str
    route: str
    raw: dict

    def get(self, key, default=None):
        return self.raw.get(key, default)


@dataclass
class Recipe:
    slug: str
    title: str
    author: str
    version: str
    category: str = "Cosmetic"
    description: str = ""
    route: str = "harness"
    ops: list = field(default_factory=list)
    path: str = None
    raw: dict = field(default_factory=dict)

    @property
    def folder(self):
        return os.path.dirname(os.path.abspath(self.path)) if self.path else None

    def resolve(self, rel):
        """A recipe-relative file path made absolute."""
        if rel is None or os.path.isabs(rel) or not self.folder:
            return rel
        return os.path.join(self.folder, rel)


def _op(raw, default_route):
    if not isinstance(raw, dict):
        return Op(kind=None, asset=None, route=default_route, raw={"_not_a_dict": raw})
    kind = raw.get("op")
    asset = raw.get("path") if kind == "legacy-replace" else raw.get("asset")
    return Op(kind=kind, asset=asset, route=raw.get("route", default_route), raw=raw)


def from_dict(data, path=None):
    route = data.get("route", "harness")
    ops = data.get("ops")
    return Recipe(slug=data.get("slug"), title=data.get("title"), author=data.get("author"),
                  version=data.get("version"), category=data.get("category", "Cosmetic"),
                  description=data.get("description", ""), route=route,
                  ops=[_op(o, route) for o in ops] if isinstance(ops, list) else ops, path=path, raw=data)


def load_recipe(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a recipe is a JSON object")
    return from_dict(data, path=path)


def is_color(value):
    return isinstance(value, str) and bool(COLOR_RE.match(value))


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_recolor(rc, where):
    if not isinstance(rc, dict):
        return [f"{where}: 'recolor' must be an object"]
    mode = rc.get("mode")
    if mode not in RECOLOR_MODES:
        return [f"{where}: recolor mode {mode!r} is not one of {', '.join(RECOLOR_MODES)}"]
    out = []
    if mode == "tint" and not is_color(rc.get("color")):
        out.append(f"{where}: tint needs 'color' as #RRGGBB, got {rc.get('color')!r}")
    if mode == "hue-shift" and not _number(rc.get("degrees")):
        out.append(f"{where}: hue-shift needs numeric 'degrees'")
    if mode == "map":
        pairs = rc.get("map")
        if not isinstance(pairs, list) or not pairs:
            out.append(f"{where}: map needs 'map': [[from, to, tolerance], ...]")
        else:
            for i, entry in enumerate(pairs):
                ok = (isinstance(entry, list) and len(entry) == 3 and is_color(entry[0]) and is_color(entry[1])
                      and _number(entry[2]) and entry[2] >= 0)
                if not ok:
                    out.append(f"{where}: map[{i}] must be ['#RRGGBB', '#RRGGBB', tolerance >= 0], got {entry!r}")
    if "keep_alpha" in rc and not isinstance(rc["keep_alpha"], bool):
        out.append(f"{where}: keep_alpha must be true or false")
    return out


def validate(recipe):
    """Problems with a recipe, as readable strings; [] when it is valid."""
    out = []
    if not isinstance(recipe.slug, str) or not SLUG_RE.match(recipe.slug):
        out.append(f"slug {recipe.slug!r} must be lower-case letters, digits and underscores")
    for key in ("title", "author", "version"):
        v = getattr(recipe, key)
        if not isinstance(v, str) or not v.strip():
            out.append(f"missing {key}")
    for key in ("category", "description"):
        if not isinstance(getattr(recipe, key), str):
            out.append(f"{key} must be a string")
    if recipe.route not in ROUTES:
        out.append(f"route {recipe.route!r} is not one of {', '.join(ROUTES)}")
    if not isinstance(recipe.ops, list) or not recipe.ops:
        out.append("no ops: add at least one operation")
        return out
    for i, op in enumerate(recipe.ops):
        where = f"ops[{i}]"
        if "_not_a_dict" in op.raw:
            out.append(f"{where}: an op must be an object")
            continue
        if op.kind not in OP_KINDS:
            out.append(f"{where}: unknown op {op.kind!r} (known: {', '.join(OP_KINDS)})")
            continue
        where = f"{where} ({op.kind})"
        if op.route not in ROUTES:
            out.append(f"{where}: route {op.route!r} is not one of {', '.join(ROUTES)}")
        if not isinstance(op.asset, str) or not op.asset.strip():
            out.append(f"{where}: missing {'path' if op.kind == 'legacy-replace' else 'asset'}")
        if op.kind == "texture-recolor":
            out += _check_recolor(op.get("recolor"), where)
        elif op.kind == "texture-replace":
            f = op.get("file")
            if not isinstance(f, str) or not f.lower().endswith(IMAGE_EXTS):
                out.append(f"{where}: 'file' must be a .png or .dds")
        elif op.kind == "ebx-set":
            if not isinstance(op.get("path"), str) or not op.get("path").strip():
                out.append(f"{where}: missing field 'path'")
            if "value" not in op.raw:
                out.append(f"{where}: missing 'value'")
            elif isinstance(op.get("value"), str) and op.get("value").startswith("#") and not is_color(op.get("value")):
                out.append(f"{where}: colour value {op.get('value')!r} must be #RRGGBB")
        elif op.kind == "legacy-replace":
            if not isinstance(op.get("file"), str) or not op.get("file").strip():
                out.append(f"{where}: missing local 'file'")
    return out


def template(slug):
    """A new recipe's starting JSON (no ops yet: `validate` asks for at least one)."""
    return {"slug": slug, "title": f"Mu {slug.replace('_', ' ').title()}", "author": "JR (Project Mu)",
            "version": "1.0", "category": "Cosmetic", "description": "", "route": "harness", "ops": []}
