"""Owner policy (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.3): pure patience, hiring, spending and meddling
terms from an owner's personality. No database, model or random reads beyond the seeded meddling draw.

  patience(owner_view)                 {hot_seat_line_scale 0.85..1.15, min_tenure_delta -1..+1}
  hiring_key(owner_view, candidate)    added to coaching_carousel.pick's score, |x| <= 0.15
  appeal_term(owner_view)              added to coaching_carousel.opening_appeal, |x| <= 0.05
  spending(owner_view)                 {cap_buffer_share 0..0.05, offers_per_wave 3..6, need_premium 0.05..0.15}
  is_big_move(move)                    a trade sending a top-10 player at his position, a top-50 free-agent
                                       signing, or a head-coach hire
  meddle_options(move, owner_view, *, vetoes_used=0)   [Option] APPROVE | VETO | PUSH via agent_loops.base;
                                       [] for a move that is not big or is on JR's team
  meddle_weights(move, owner_view)     {APPROVE, VETO, PUSH: weight} for base.policy_choice
  meddle_choice(move, owner_view, *, season, tick, vetoes_used=0)   the seeded policy key, or None (no review)

owner_view is a personality view or persona ({traits, sliders}); None or an empty view is a neutral owner, whose
terms are today's constants (scale 1.0, no hiring or appeal term, transactions_engine's spending constants, and
p_veto 0.05). JR's teams: the owner never overrules (meddling returns no options) and never fires (the wiring keeps
the engines' user_controlled rules; patience only scales CPU teams' lines). Every constant is a review-only
estimate, cleared through the activation plan's shadow season.
"""
from __future__ import annotations

from engines.agent_loops import base

LOOP_KIND = "OWNER_MEDDLE"

# patience
LINE_SCALE = (0.85, 1.15)
PATIENT_LINE = 0.10
HEADLINE_LINE = -0.10
SLIDER_LINE = 0.01            # per point from 5: a volatile temperament and a high drive each lower the line
TENURE_DELTA = (-1, 1)

# hiring
HIRING_CAP = 0.15
HEADLINE_RETREAD = 0.15       # a former head coach / retread
PATIENT_YOUNG_COORD = 0.10    # a coordinator under YOUNG_AGE
YOUNG_AGE = 45
OPENNESS_FIRST_TIMER = 0.01   # per openness point from 5, for a first-time head coach
APPEAL_CAP = 0.05
MEDDLER_APPEAL = -0.05

# spending (today's constants: engines/transactions_engine.py CAP_BUFFER_SHARE, OFFERS_PER_WAVE, OFFER_NEED_PREMIUM)
DEFAULT_SPENDING = {"cap_buffer_share": 0.02, "offers_per_wave": 4, "need_premium": 0.10}
DEEP_POCKETS_SPENDING = {"cap_buffer_share": 0.0, "offers_per_wave": 6, "need_premium": 0.15}
BUDGET_SPENDING = {"cap_buffer_share": 0.05, "offers_per_wave": 3, "need_premium": 0.05}

# meddling
MOVE_KINDS = ("TRADE", "SIGNING", "HIRE")
DECISIONS = ("APPROVE", "VETO", "PUSH")
TRADE_TOP_AT_POSITION = 10
SIGNING_TOP = 50
FAN_FAVOURITE_TOP = 20        # player_popularity_engine rank
P_VETO_BASE = 0.05
P_VETO_MEDDLER = 0.20
P_VETO_HANDS_OFF = -0.04
P_VETO_FAN_FAVOURITE = 0.05
P_VETO_MAX = 0.5
P_PUSH = 0.15                 # only HEADLINE_HUNTER + MEDDLER owners push, and only with a legal alternative
VETO_CAP = 1                  # vetoes per team per season (R2)


def _traits(view):
    return base.traits_of(view)


def _slider(view, name):
    return base.slider(view, name)


def patience(owner_view) -> dict:
    traits = _traits(owner_view)
    scale = 1.0
    scale += PATIENT_LINE if "PATIENT_BUILDER" in traits else 0.0
    scale += HEADLINE_LINE if "HEADLINE_HUNTER" in traits else 0.0
    scale -= SLIDER_LINE * (_slider(owner_view, "temperament") - 5)
    scale -= SLIDER_LINE * (_slider(owner_view, "drive") - 5)
    delta = 1 if "PATIENT_BUILDER" in traits else 0
    return {"hot_seat_line_scale": round(base.clamp(scale, *LINE_SCALE), 4),
            "min_tenure_delta": int(base.clamp(delta, *TENURE_DELTA))}


def _former_head_coach(candidate):
    return candidate.get("kind") == "RETREAD" or bool(candidate.get("former_head_coach"))


def hiring_key(owner_view, candidate) -> float:
    """candidate: a coaching_carousel candidate ({key, kind POACHED|RETREAD|..., score, age?, former_head_coach?})."""
    traits = _traits(owner_view)
    x = 0.0
    former = _former_head_coach(candidate)
    if "HEADLINE_HUNTER" in traits and former:
        x += HEADLINE_RETREAD
    if not former:
        x += OPENNESS_FIRST_TIMER * (_slider(owner_view, "openness") - 5)
    age = candidate.get("age")
    if ("PATIENT_BUILDER" in traits and candidate.get("kind") == "POACHED" and isinstance(age, (int, float))
            and age < YOUNG_AGE):
        x += PATIENT_YOUNG_COORD
    return round(base.clamp(x, -HIRING_CAP, HIRING_CAP), 4)


def appeal_term(owner_view) -> float:
    x = MEDDLER_APPEAL if "MEDDLER" in _traits(owner_view) else 0.0
    return round(base.clamp(x, -APPEAL_CAP, APPEAL_CAP), 4)


def spending(owner_view) -> dict:
    traits = _traits(owner_view)
    if "DEEP_POCKETS" in traits:
        return dict(DEEP_POCKETS_SPENDING)
    if "BUDGET_CONSCIOUS" in traits:
        return dict(BUDGET_SPENDING)
    return dict(DEFAULT_SPENDING)


# ------------------------------------------------------------------------------------------------ meddling ---

def is_big_move(move) -> bool:
    """move: {kind TRADE|SIGNING|HIRE, position_rank (TRADE: the best player sent, rank at his position),
    fa_rank (SIGNING), role (HIRE: 'HC' for a head coach)}."""
    kind = (move or {}).get("kind")
    if kind == "TRADE":
        r = move.get("position_rank")
        return isinstance(r, int) and r <= TRADE_TOP_AT_POSITION
    if kind == "SIGNING":
        r = move.get("fa_rank")
        return isinstance(r, int) and r <= SIGNING_TOP
    if kind == "HIRE":
        return move.get("role", "HC") == "HC"
    return False


def p_veto(move, owner_view) -> float:
    traits = _traits(owner_view)
    p = P_VETO_BASE
    p += P_VETO_MEDDLER if "MEDDLER" in traits else 0.0
    p += P_VETO_HANDS_OFF if "HANDS_OFF" in traits else 0.0
    pop = (move or {}).get("popularity_rank")
    if isinstance(pop, int) and pop <= FAN_FAVOURITE_TOP:
        p += P_VETO_FAN_FAVOURITE
    return round(base.clamp(p, 0.0, P_VETO_MAX), 4)


def can_push(move, owner_view) -> bool:
    traits = _traits(owner_view)
    return ({"HEADLINE_HUNTER", "MEDDLER"} <= traits and (move or {}).get("kind") in ("SIGNING", "HIRE")
            and bool(move.get("alternative")))


def meddle_options(move, owner_view, *, vetoes_used=0) -> list:
    """The owner's choices on a CPU team's big move, as agent-loop Options. [] when the move isn't big or is on JR's
    team (move['user_controlled']): the owner never reviews JR's moves."""
    if not move or move.get("user_controlled") or move.get("kind") not in MOVE_KINDS or not is_big_move(move):
        return []
    ref = {k: move[k] for k in ("kind", "team_id", "ref") if k in move}
    opts = [base.Option("APPROVE", "APPROVE", ref, "Let the move stand")]
    if vetoes_used < VETO_CAP:
        opts.append(base.Option("VETO", "VETO", ref, "Block the move"))
    if can_push(move, owner_view):
        opts.append(base.Option("PUSH", "PUSH", {**ref, "alternative": move["alternative"]},
                                "Push for the owner's own choice instead"))
    return opts


def meddle_weights(move, owner_view) -> dict:
    veto = p_veto(move, owner_view)
    push = P_PUSH if can_push(move, owner_view) else 0.0
    return {"APPROVE": round(max(0.0, 1.0 - veto - push), 4), "VETO": veto, "PUSH": push}


def meddle_choice(move, owner_view, *, season, tick, vetoes_used=0):
    """The seeded policy decision (rng 'loop:{season}:{tick}:OWNER_MEDDLE:{team}:{ref}'), or None when the owner
    doesn't review this move."""
    opts = meddle_options(move, owner_view, vetoes_used=vetoes_used)
    if not opts:
        return None
    subject = f"{move.get('team_id')}:{move.get('kind')}:{move.get('ref')}"
    rng = base.rng_for(season, tick, LOOP_KIND, subject)
    return base.policy_choice(opts, owner_view, meddle_weights(move, owner_view), rng)
