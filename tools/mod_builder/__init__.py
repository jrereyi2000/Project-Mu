"""Project Mu mod builder (docs/in_progress/MOD_BUILDER_PLAN.md): recipes, asset index, recolouring, install.

    python3 -m tools.mod_builder find <pattern> | new <slug> | build <slug> [--route harness|gui] [--dry-run]
                                 | install <slug> [--pack NAME] | check <slug>

Build folders live in data/mod_builds/<slug>/ (MU_MOD_BUILDS overrides the root). Only MMC Editor's own export
(through tools/mmc_harness or the editor's window) writes the .fbmod; nothing here writes mods.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def builds_dir():
    return os.environ.get("MU_MOD_BUILDS") or os.path.join(ROOT, "data", "mod_builds")


def build_dir(slug):
    return os.path.join(builds_dir(), slug)


def mod_file_name(slug):
    """'Mu_YellowFirstDown.fbmod' for 'yellow_first_down': the name a build is installed under (plan Q4)."""
    return "Mu_" + "".join(w[:1].upper() + w[1:] for w in slug.replace("-", "_").split("_") if w) + ".fbmod"
