"""Install a built mod into the MMC Mod Manager and read a build back (MOD_BUILDER_PLAN §2.6).

`install` copies the .fbmod into the Mod Manager's Mods folder (MMC_MANAGER_DIR/Mods/Madden27) under a
`Mu_` name and writes a pack (`Mu Test` by default): JR's `Mu Base` pack with the new mod enabled at the top
(pack strings run lowest priority first, so it goes last and wins any shared asset). It refuses while
MMCModManager.exe is open, only ever writes files whose name starts with `Mu_`, and copies
manager_config.json to manager_config.backup-<time>-before-<pack>.json first. The pack helpers are
pipeline/mod_packs.py's own.

`check` parses the built mod with tools/fbmod_inspect.parse_fbmod and compares it with the recipe.
"""
import datetime as dt
import os
import re
import shutil

from pipeline import mod_packs
from tools import fbmod_inspect
from tools.mod_builder import mod_file_name

OUR_PREFIX = "Mu_"
DEFAULT_PACK = "Mu Test"


class InstallError(RuntimeError):
    pass


def mods_dir():
    return os.path.join(os.environ.get("MMC_MANAGER_DIR", mod_packs.DEFAULT_MANAGER_DIR), "Mods", "Madden27")


def target_name(fbmod_path):
    """The file name a build installs as: its own if it is already Mu_..., else Mu_<CamelCase stem>.fbmod."""
    base = os.path.basename(fbmod_path)
    if base.startswith(OUR_PREFIX):
        return base
    return mod_file_name(os.path.splitext(base)[0])


def backup_config(path, pack):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = re.sub(r"[^A-Za-z0-9]+", "-", pack).strip("-").lower() or "pack"
    stem, ext = os.path.splitext(path)
    dst = f"{stem}.backup-{stamp}-before-{tag}{ext}"
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = f"{stem}.backup-{stamp}-{n}-before-{tag}{ext}"
    shutil.copy2(path, dst)
    return dst


def install(fbmod_path, pack=DEFAULT_PACK, name=None, mods_folder=None, config=None):
    """Copy the mod in and write `pack` = Mu Base + this mod on top. Returns what it did."""
    if not os.path.isfile(fbmod_path):
        raise InstallError(f"no built mod at {fbmod_path}")
    name = name or target_name(fbmod_path)
    if not name.startswith(OUR_PREFIX) or os.path.basename(name) != name or not name.endswith(".fbmod"):
        raise InstallError(f"refusing to install as {name!r}: our mods are Mu_<Name>.fbmod, and nothing else "
                           "in the Mods folder is ever overwritten")
    if pack == mod_packs.BASE_PACK:
        raise InstallError(f"refusing to write '{mod_packs.BASE_PACK}': JR keeps that pack by hand")
    if mod_packs.manager_running():
        raise InstallError("MMCModManager.exe is open; close it first (it saves its own copy of the packs)")
    folder = mods_folder or mods_dir()
    if not os.path.isdir(folder):
        raise InstallError(f"no Mods folder at {folder} (set MMC_MANAGER_DIR)")
    dest = os.path.join(folder, name)
    if os.path.lexists(dest) and (os.path.islink(dest) or not os.path.isfile(dest)):
        raise InstallError(f"{dest} exists and isn't a plain file; not touching it")
    cfg = config or mod_packs.config_path()
    entries = [(n, on) for n, on in mod_packs.base_pack(cfg) if n != name] + [(name, True)]
    backup = backup_config(cfg, pack)
    replaced = os.path.exists(dest)
    if os.path.abspath(fbmod_path) != os.path.abspath(dest):
        shutil.copyfile(fbmod_path, dest)
    mod_packs.write_pack(cfg, pack, entries, select=True)
    return {"mod": dest, "replaced": replaced, "pack": pack, "config": cfg, "backup": backup,
            "mods_in_pack": len(entries), "enabled": sum(1 for _, on in entries if on)}


# ------------------------------------------------------------------------------------------------ check ---
def _expected(recipe):
    out = {}
    for op in recipe.ops:
        if op.asset:
            out[op.asset.lower()] = op.kind
    return out


def check(fbmod_path, recipe):
    """Read the built mod back: every recipe asset present, nothing else (apart from their chunks and the
    legacy collectors), details as in the recipe, every payload FMENC001-encrypted."""
    mod = fbmod_inspect.parse_fbmod(fbmod_path)
    expected = _expected(recipe)
    present, extra, plain = set(), [], []
    for res in mod["resources"]:
        if res["index"] == -1:
            continue
        if res.get("payload_magic") is not None and res["payload_magic"] != fbmod_inspect.ENCRYPTED_MAGIC:
            plain.append(res["name"])
        if res["type"] == "embedded":
            continue
        ud = res.get("user_data") or ""
        if ud.startswith("legacy;Collector"):
            continue
        if ud.startswith("legacy;"):
            key = ud[7:].lower()
        elif res["type"] == "chunk":
            owner = (res.get("owner") or {}).get("name", "").lower()
            if owner not in expected:      # a chunk belongs only when its owner is a recipe asset
                extra.append(res["name"])
            continue
        else:
            key = res["name"].lower()
        if key in expected:
            present.add(key)
        else:
            extra.append(ud[7:] if ud.startswith("legacy;") else res["name"])
    missing = sorted(a for a in expected if a not in present)
    details = {}
    for key in ("title", "author", "version", "category"):
        want, got = getattr(recipe, key), mod.get(key)
        if key == "category" and not want:
            continue
        if want != got:
            details[key] = {"recipe": want, "mod": got}
    result = {
        "fbmod": os.path.abspath(fbmod_path),
        "title": mod.get("title"), "author": mod.get("author"), "version": mod.get("version"),
        "game_version": mod.get("game_version"),
        "resources": len(mod["resources"]),
        "missing": missing, "extra": sorted(set(extra)), "details_mismatch": details,
        "unencrypted": sorted(set(plain)),
    }
    result["ok"] = not (missing or extra or details or plain)
    return result
