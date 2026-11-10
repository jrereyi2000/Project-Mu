"""
Front-office inbox (docs/done/FRONT_OFFICE_INBOX_PLAN.md, V2.9).

JR plays through personas he explicitly takes control of (a team's GM, its head coach, later a player). Each
thread is one conversation between a persona JR controls and a counterpart agent. Engines decide every outcome;
the counterpart's words come from the model in its own voice (checked against the facts) or a template.

  actors.py     which personas JR controls (controlled_personas; no default, D8)
  store.py      threads, messages, caps, expiry, read-only rule, jr_statements
  registry.py   Trigger, ActResult, the Kind protocol, kinds() auto-discovered from engines/inbox/kinds/
  voice.py      the counterpart's words (+ its private appraisal) within INBOX_LLM_BUDGET_S, template fallback
  appraisal.py  relationship events in PERSONALITY_RELATIONSHIPS_PLAN §2.2's envelope, held in an outbox
  parse.py      typed text -> a draft JR confirms
  scan.py       inbound messages for every controlled actor; step_inbox for the weekly run
  __main__.py   CLI: python3 -m engines.inbox scan|list|show|control|flush-events

Nothing here commits: the caller owns the transaction (the CLI and step_inbox commit their own).
"""


class InboxError(Exception):
    """An inbox refusal with the HTTP status the API maps it to (400, 403, 404, 409)."""

    status = 400

    def __init__(self, status=None, message=""):
        if isinstance(status, str) and not message:          # InboxError("text") keeps the class status
            status, message = None, status
        if status is not None:
            self.status = int(status)
        self.message = message
        super().__init__(message)


class NotUserTeam(InboxError):
    """The team isn't one of the active franchise registry's user_teams (D6)."""
    status = 403


class NotFound(InboxError):
    status = 404


class Conflict(InboxError):
    """The persona isn't on that team, the thread is read-only, or a cap is reached."""
    status = 409
