"""
Conversation kinds (FRONT_OFFICE_INBOX_PLAN.md §2.7).

A kind is one module in engines/inbox/kinds/. Sibling plans add a kind by adding one file; nothing here changes.
kinds() imports every module in that package (pkgutil) and keeps those that define a KIND:
  - a module-level KIND makes the module itself the kind (its functions are the protocol's methods);
  - otherwise a class defined in the module with a string KIND is instantiated with no arguments.
Modules with neither are ignored (helpers). Two modules with the same KIND raise RegistryError.

ACTOR_ROLES says which controlled personas a kind serves (('GM', 'COACH') when unsure). A kind without it is
scanned once per team, with the team's first actor (or the Trigger's own actor_role 'TEAM' for a notice).

Optional hooks a kind may define (scan.py and voice.py call them when present):
  validate(text, facts) -> bool | (ok, why)        extra check on the model's words
  draft(cur, thread, text, llm=None) -> {"option_id", "payload", "summary"} | None
  review_promises(cur, season, week, team) -> int  due-promise review in the weekly scan (ROLE_TALK)
  PERSONA_ROLE: str | None                         the personality role voice.persona_view reads (default:
                                                   from COUNTERPART_TYPE; None -> neutral)
"""
from dataclasses import dataclass
import importlib
import inspect
import pkgutil
from typing import Protocol

KINDS_PACKAGE = "engines.inbox.kinds"
DEFAULT_ACTOR_ROLES = ("GM", "COACH")
COUNTERPART_TYPES = ("gm", "coach", "player", "writer", "prospect", "scout", "owner", "system")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Trigger:
    kind: str
    team_id: int
    counterpart_type: str
    counterpart_id: str
    counterpart_name: str
    subject: str
    source_ref: str
    facts: dict
    intent: str
    link_table: str | None = None
    link_id: int | None = None
    expires_season: int | None = None
    expires_week: int | None = None
    actor_role: str | None = None
    actor_id: str | None = None
    state: dict | None = None


@dataclass
class ActResult:
    facts: dict
    intent: str
    engine_ref: dict | None
    close: bool = False
    link: tuple | None = None
    state: dict | None = None
    judge: bool = True               # False: the counterpart doesn't judge this exchange (e.g. a SYSTEM line)


class Kind(Protocol):
    KIND: str
    COUNTERPART_TYPE: str
    ACTOR_ROLES: tuple = DEFAULT_ACTOR_ROLES     # optional; missing -> once per team with its first actor, or 'TEAM'

    def scan(self, cur, season, week, team) -> list[Trigger]: ...       # team carries 'actor'
    def options(self, cur, thread, last) -> list[dict]: ...
    def act(self, cur, thread, option_id, payload, note) -> ActResult: ...
    def template(self, intent, facts) -> str: ...
    def start(self, cur, team, body) -> Trigger: ...


_CACHE = {}


def _kind_of(module):
    if isinstance(getattr(module, "KIND", None), str):
        return module
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__ and isinstance(getattr(obj, "KIND", None), str):
            return obj()
    return None


def discover(package=KINDS_PACKAGE):
    """{KIND: kind} for every module of the package that defines one."""
    pkg = importlib.import_module(package)
    found, where = {}, {}
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        kind = _kind_of(module)
        if kind is None:
            continue
        key = kind.KIND
        if key in found:
            raise RegistryError(f"inbox kind {key} is defined twice: {where[key]} and {module.__name__}")
        found[key], where[key] = kind, module.__name__
    return found


def kinds(package=KINDS_PACKAGE, refresh=False):
    """The registered kinds, discovered once per process (refresh=True rescans)."""
    if refresh or package not in _CACHE:
        _CACHE[package] = discover(package)
    return dict(_CACHE[package])


def get(kind_name, package=KINDS_PACKAGE):
    return kinds(package).get(kind_name)


def actor_roles(kind):
    """The kind's ACTOR_ROLES tuple, or None when it doesn't declare one."""
    roles = getattr(kind, "ACTOR_ROLES", None)
    return tuple(roles) if roles is not None else None


def serves(kind, role):
    roles = actor_roles(kind)
    return roles is None or role in roles


def actors_to_scan(kind, team_actors):
    """Which of a team's controlled actors a kind is scanned with: each actor whose role it serves, or the first
    actor once when it declares no ACTOR_ROLES. No actors -> none (D8)."""
    if not team_actors:
        return []
    roles = actor_roles(kind)
    if roles is None:
        return [team_actors[0]]
    return [a for a in team_actors if a.role in roles]
