"""CLI for the mod builder (MOD_BUILDER_PLAN §2.7).

    python3 -m tools.mod_builder find <pattern> [--kind ebx|res|bundle|legacy] [--limit N]
    python3 -m tools.mod_builder new <slug>
    python3 -m tools.mod_builder build <slug> [--route harness|gui] [--dry-run]
    python3 -m tools.mod_builder check <slug>
    python3 -m tools.mod_builder install <slug> [--pack NAME]

`build --dry-run` prints the harness commands without running them. `build --route gui` clicks nothing: it
prints the MMC Editor steps (and does the local recolour for any texture already exported into work/).
Harness builds need tools/mod_builder/harness.py (tools/mmc_harness); every call is logged to build.log and
the result is read back into check.json.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

from tools.mod_builder import build_dir, mod_file_name
from tools.mod_builder import recipe as recipe_mod

HARNESS_OPS = ("texture-recolor", "texture-replace", "ebx-set")   # op kinds with a harness command (§2.4)


# ---------------------------------------------------------------------------------------------- paths ---
def paths(slug):
    folder = build_dir(slug)
    return {"folder": folder, "recipe": os.path.join(folder, "recipe.json"), "work": os.path.join(folder, "work"),
            "project": os.path.join(folder, f"{slug}.fbproject"), "fbmod": os.path.join(folder, f"{slug}.fbmod"),
            "log": os.path.join(folder, "build.log"), "check": os.path.join(folder, "check.json")}


def winpath(path):
    """Windows form of a WSL path, for steps JR or the editor reads (falls back to the path as given)."""
    try:
        out = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return path


def _stem(asset):
    return asset.rstrip("/").rsplit("/", 1)[-1]


# ------------------------------------------------------------------------------------------------ plan ---
def _route(op, route, force=False):
    """An op's route in a build: `--route` (force) sets every op; otherwise the op's own `route` key wins
    over the recipe's. Ops with no harness command always go through the window."""
    r = route if force else op.raw.get("route", route)
    return "gui" if op.kind not in HARNESS_OPS else r


def plan_build(rcp, route, p, force=False):
    """The build as a list of steps: {"kind": "harness", "cmd": [...]}, {"kind": "local", "action": ...,
    "src", "dst", ...} or {"kind": "gui", "text": ...}."""
    project, work = p["project"], p["work"]
    harness_ops = [op for op in rcp.ops if _route(op, route, force) == "harness"]
    gui_ops = [op for op in rcp.ops if _route(op, route, force) == "gui"]
    steps = []
    if harness_ops:
        steps.append({"kind": "harness", "cmd": ["info"]})
    for op in harness_ops:
        stem = _stem(op.asset)
        if op.kind == "texture-recolor":
            png = os.path.join(work, f"{stem}.png")
            out_png = os.path.join(work, f"{stem}_recolor.png")
            dds = os.path.join(work, f"{stem}_recolor.dds")
            steps += [{"kind": "harness", "cmd": ["export-texture", op.asset, png]},
                      {"kind": "local", "action": "recolor", "src": png, "dst": out_png, "spec": op.get("recolor")},
                      {"kind": "local", "action": "png_to_dds", "src": out_png, "dst": dds},
                      {"kind": "harness", "cmd": ["import-texture", project, op.asset, dds]}]
        elif op.kind == "texture-replace":
            src = rcp.resolve(op.get("file"))
            if src.lower().endswith(".png"):
                dds = os.path.join(work, f"{stem}_replace.dds")
                steps.append({"kind": "local", "action": "png_to_dds", "src": src, "dst": dds})
                src = dds
            steps.append({"kind": "harness", "cmd": ["import-texture", project, op.asset, src]})
        elif op.kind == "ebx-set":
            value = op.get("value")
            value = value if isinstance(value, str) else json.dumps(value)
            steps.append({"kind": "harness", "cmd": ["ebx-set", project, op.asset, op.get("path"), value]})
    if gui_ops:
        steps += _gui_steps(rcp, gui_ops, p, project_exists=bool(harness_ops))
    else:
        steps.append({"kind": "harness", "cmd": [
            "export-mod", project, p["fbmod"], "--title", rcp.title, "--author", rcp.author,
            "--version", rcp.version, "--category", rcp.category or "Cosmetic",
            "--description", rcp.description or ""]})
    return steps


def _gui_steps(rcp, ops, p, project_exists):
    w = winpath
    g = lambda text: {"kind": "gui", "text": text}   # noqa: E731
    steps = [g("Start MMC Editor (Start menu: MMC Editor, no admin) and open the Madden27 profile")]
    if project_exists or os.path.exists(p["project"]):
        steps.append(g(f"File > Open Project: {w(p['project'])}"))
    else:
        steps.append(g("File > New Project"))
    for op in ops:
        if op.kind == "texture-recolor":
            png = os.path.join(p["work"], f"{_stem(op.asset)}.png")
            out_png = os.path.join(p["work"], f"{_stem(op.asset)}_recolor.png")
            steps += [g(f"Data Explorer: find {op.asset}, open it, Export as PNG to {w(png)}"),
                      {"kind": "local", "action": "recolor", "src": png, "dst": out_png, "spec": op.get("recolor"),
                       "text": f"run `python3 -m tools.mod_builder build {rcp.slug} --route gui` again to "
                               f"recolour it into {w(out_png)}"},
                      g(f"Import {w(out_png)} into {op.asset}")]
        elif op.kind == "texture-replace":
            steps.append(g(f"Data Explorer: find {op.asset}, open it, Import {w(rcp.resolve(op.get('file')))}"))
        elif op.kind == "ebx-set":
            steps.append(g(f"Data Explorer: find {op.asset}, open it; in the property grid set "
                           f"{op.get('path')} to {op.get('value')}"))
        elif op.kind == "legacy-replace":
            steps.append(g(f"Legacy Explorer: find {op.asset}, Import {w(rcp.resolve(op.get('file')))}"))
    steps += [g(f"File > Save Project as {w(p['project'])}"),
              g(f"File > Export Mod: title '{rcp.title}', author '{rcp.author}', version '{rcp.version}', "
                f"category '{rcp.category or 'Cosmetic'}', description '{rcp.description or ''}'; "
                f"save as {w(p['fbmod'])}"),
              g(f"Then: python3 -m tools.mod_builder check {rcp.slug}")]
    return steps


def describe(step):
    if step["kind"] == "harness":
        return "harness: MmcHarness.exe " + " ".join(_quote(a) for a in step["cmd"])
    if step["kind"] == "local":
        if step["action"] == "recolor":
            return f"local:   recolor {json.dumps(step['spec'])} {step['src']} -> {step['dst']}"
        return f"local:   png_to_dds {step['src']} -> {step['dst']}"
    return f"gui:     {step['text']}"


def _quote(a):
    a = str(a)
    return f'"{a}"' if (" " in a or not a) else a


def run_local(step):
    from tools.mod_builder import recolor
    os.makedirs(os.path.dirname(step["dst"]), exist_ok=True)
    if step["action"] == "recolor":
        recolor.recolor_png(step["src"], step["dst"], step["spec"])
    else:
        recolor.png_to_dds(step["src"], step["dst"])


def _load_harness():
    import importlib
    try:
        return importlib.import_module("tools.mod_builder.harness")
    except ModuleNotFoundError as e:
        if e.name == "tools.mod_builder.harness":
            return None
        raise


def _log(p, line):
    os.makedirs(p["folder"], exist_ok=True)
    with open(p["log"], "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")


# ----------------------------------------------------------------------------------------------- cmds ---
def _load(slug):
    p = paths(slug)
    if not os.path.exists(p["recipe"]):
        raise SystemExit(f"no recipe at {p['recipe']}: python3 -m tools.mod_builder new {slug}")
    rcp = recipe_mod.load_recipe(p["recipe"])
    problems = recipe_mod.validate(rcp)
    if problems:
        raise SystemExit("recipe problems:\n" + "\n".join(f"  - {x}" for x in problems))
    return rcp, p


def cmd_find(args):
    from tools.mod_builder import asset_index
    rows = asset_index.find(args.pattern, kind=args.kind, limit=args.limit, log=asset_index._log)
    for r in rows:
        print(f"{r['kind']:<7} {r['type'] or '-':<28} {r['name']}")
    if not rows:
        print(f"nothing matches {args.pattern!r}")
        return 1
    return 0


def cmd_new(args):
    if not recipe_mod.SLUG_RE.match(args.slug):
        raise SystemExit("slug: lower-case letters, digits and underscores")
    p = paths(args.slug)
    if os.path.exists(p["recipe"]):
        raise SystemExit(f"{p['recipe']} already exists")
    os.makedirs(p["work"], exist_ok=True)
    with open(p["recipe"], "w", encoding="utf-8") as f:
        json.dump(recipe_mod.template(args.slug), f, indent=2)
        f.write("\n")
    print(f"wrote {p['recipe']}; add ops (find assets with: python3 -m tools.mod_builder find <pattern>)")
    return 0


def cmd_build(args):
    rcp, p = _load(args.slug)
    route = args.route or rcp.route
    steps = plan_build(rcp, route, p, force=bool(args.route))
    if args.dry_run:
        print(f"{rcp.slug}: {len(steps)} steps, route {route} (dry run)")
        for s in steps:
            print("  " + describe(s))
        return 0
    os.makedirs(p["work"], exist_ok=True)
    if route == "gui" or all(s["kind"] != "harness" for s in steps):
        print(f"{rcp.slug}: MMC Editor steps (nothing is clicked here)")
        for i, s in enumerate(steps, 1):
            if s["kind"] == "local":
                if os.path.exists(s["src"]):
                    run_local(s)
                    _log(p, f"local {s['action']} {s['src']} -> {s['dst']} ok")
                    print(f"  {i}. done here: recoloured {s['src']} -> {s['dst']}")
                else:
                    print(f"  {i}. {s['text']}")
            else:
                print(f"  {i}. {s['text']}")
        return 0
    harness = _load_harness()
    if harness is None:
        print("the console harness isn't installed yet (tools/mod_builder/harness.py, tools/mmc_harness/); "
              f"use --dry-run to see its commands, or --route gui")
        return 2
    _log(p, f"build {rcp.slug} route {route}")
    gui_left = []
    for s in steps:
        if s["kind"] == "gui":
            gui_left.append(s)
            continue
        if s["kind"] == "local":
            run_local(s)
            _log(p, f"local {s['action']} {s['src']} -> {s['dst']} ok")
            print(describe(s))
            continue
        print(describe(s), flush=True)
        try:
            out = harness.run(s["cmd"], timeout=600)
        except harness.HarnessError as e:
            _log(p, f"harness {json.dumps(s['cmd'])} -> error {e}")
            print(f"harness error: {e}")
            return 1
        _log(p, f"harness {json.dumps(s['cmd'])} -> {json.dumps(out)}")
    if gui_left:
        print("the rest goes through MMC Editor's window:")
        for i, s in enumerate(gui_left, 1):
            print(f"  {i}. {s['text']}")
        return 0
    return _check(rcp, p)


def _check(rcp, p):
    from tools.mod_builder.install import check
    if not os.path.exists(p["fbmod"]):
        raise SystemExit(f"no built mod at {p['fbmod']}")
    result = check(p["fbmod"], rcp)
    with open(p["check"], "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    _print_check(result)
    print(f"wrote {p['check']}")
    return 0 if result["ok"] else 1


def _print_check(r):
    print(f"{'OK' if r['ok'] else 'PROBLEMS'}: {r['title']} v{r['version']} by {r['author']}, build "
          f"{r['game_version']}, {r['resources']} resources")
    for key in ("missing", "extra", "unencrypted"):
        for name in r[key]:
            print(f"  {key}: {name}")
    for key, v in r["details_mismatch"].items():
        print(f"  {key}: recipe {v['recipe']!r}, mod {v['mod']!r}")


def cmd_check(args):
    rcp, p = _load(args.slug)
    return _check(rcp, p)


def cmd_install(args):
    from tools.mod_builder import install as inst
    rcp, p = _load(args.slug)
    if _check(rcp, p) != 0:
        print("not installing: the read-back check failed")
        return 1
    try:
        out = inst.install(p["fbmod"], pack=args.pack, name=mod_file_name(rcp.slug))
    except inst.InstallError as e:
        print(f"not installed: {e}")
        return 1
    print(f"{'replaced' if out['replaced'] else 'copied'} {out['mod']}")
    print(f"wrote pack '{out['pack']}' ({out['enabled']} of {out['mods_in_pack']} mods on, this one on top) and "
          f"selected it; config backup {out['backup']}")
    print(f"In game: start MMC Mod Manager, pick '{out['pack']}', Launch, and check: {rcp.description or rcp.title}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m tools.mod_builder", description="Build Madden 27 mods")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="search the game's asset names (MMC Editor's cache)")
    f.add_argument("pattern", help="substring, or a glob (* ? [) on the whole name; case-blind")
    f.add_argument("--kind", choices=("ebx", "res", "bundle", "legacy", "unknown"))
    f.add_argument("--limit", type=int, default=50)
    n = sub.add_parser("new", help="start data/mod_builds/<slug>/recipe.json")
    n.add_argument("slug")
    b = sub.add_parser("build", help="build a recipe (harness, or MMC Editor steps)")
    b.add_argument("slug")
    b.add_argument("--route", choices=recipe_mod.ROUTES, help="override the recipe's route")
    b.add_argument("--dry-run", action="store_true", help="print the planned harness commands only")
    c = sub.add_parser("check", help="read the built mod back against the recipe (writes check.json)")
    c.add_argument("slug")
    i = sub.add_parser("install", help="copy into the Mod Manager and write a pack (Mod Manager closed)")
    i.add_argument("slug")
    i.add_argument("--pack", default="Mu Test")
    args = ap.parse_args(argv)
    return {"find": cmd_find, "new": cmd_new, "build": cmd_build, "check": cmd_check,
            "install": cmd_install}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
