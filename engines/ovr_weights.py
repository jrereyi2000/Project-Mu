"""
Madden 27 OVR formulas (docs/in_progress/PC_MIGRATION_PLAN.md §4.4): fits how Madden turns a player's ratings into each archetype's
OVR, from a save-reader snapshot (pipeline/save_reader), and moves a player's ratings to a target OVR for the
teleporter (§4.3 step 2).

  python3 -m engines.ovr_weights fit <snapshot.json> [--out engines/ovr_weights_m27.json]
  python3 -m engines.ovr_weights show [--weights engines/ovr_weights_m27.json]

How Madden does it (CAREER-GIANTS-AUTOSAVE): every player stores an OVR per archetype of their position
(Player.OverallGrade0-4, the snapshot's `archetype_grades`). OverallRating is the highest of them (2,984 of 2,990
players; the rest have no grades yet) and PlayerType is that archetype's name. On a tie the stored archetype is always
one of the tied ones, but not by slot order (likely a player keeps their archetype until another is strictly higher).

Fitting: slots are mapped to archetypes per position from the players whose top grade is unique. Each archetype is fitted
on every grade of it across its positions (9,106 samples instead of 2,990 OVRs), by non-negative least squares, either
linear (grade = intercept + Σ w·rating) or with squared terms (+ Σ q·rating²/100, q ≥ 0), whichever cross-validates
better. Both are non-decreasing in every rating, so raising a rating never lowers a grade. Overall: ~91% exact,
~100% within ±1 (5-fold cross-validated); taking the highest predicted grade gives the stored OVR for ~95% of players
exactly and ~100% within ±1.

QB refit (Item 1, PRE_PC_WORK.md): In the original fit, QB formulas spuriously latched onto LongSnap (worth ~1.5 OVR per
LongSnap²/100) due to a single anomalous converted receiver in the sample save (Carter Landry, pgid 24437) whose stale
grades stayed flat at 83 with LongSnap 59 while all other QBs had LongSnap 0 or 10. On real/PS5-scanned QBs with LongSnap
~46, this term exploded, predicting QBs 30-50 points too high. Excluding anomalous QB records whose grades disagree with
OverallRating and removing LongSnap from QB features yields 100% within ±1 on the sample save (cv_exact 90-93%), predicts
100% (78/78) of PS5-scanned QBs within ±1 (93.6% exact), and keeps all non-QB archetypes unchanged. LongSnap is also
clamped to 10 for QBs during prediction as a guard against legacy weights.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import nnls

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ovr_weights_m27.json")
SUPPORT = 0.01          # a linear weight above this keeps the rating in the archetype's quadratic fit
SKILL_MEDIAN = 40       # adjust_ratings only moves ratings whose median among the archetype's players is at least this
FOLDS = 5
RATING_MAX = 99


# ------------------------------------------------------------------- fitting ----
def slot_map(players):
    """{position: [archetype per grade slot]} from the players whose top grade is unique (the slot holding it is the
    stored PlayerType). A slot no player tops in this save stays None."""
    votes = defaultdict(Counter)
    for p in players:
        g = p.get("archetype_grades") or []
        top = max(g, default=0)
        if top and g.count(top) == 1:
            votes[p["position"]][(g.index(top), p["archetype"])] += 1
    slots = {}
    for pos, c in votes.items():
        width = max(len(p["archetype_grades"]) for p in players if p["position"] == pos)
        out = [None] * width
        for (i, arch), _ in c.most_common():
            if out[i] is None and arch not in out:
                out[i] = arch
        slots[pos] = out
    return slots


def grade_samples(players, slots):
    """{archetype: [(ratings, grade)]}: every non-zero grade of every player whose position has that archetype."""
    out = defaultdict(list)
    for p in players:
        for i, grade in enumerate(p.get("archetype_grades") or []):
            arch = slots.get(p["position"], [None] * 5)[i] if i < len(slots.get(p["position"], [])) else None
            if arch and grade > 0:
                out[arch].append((p["ratings"], grade))
    return out


def _design(rows, linear, squared):
    X = np.array([[r.get(k, 0) for k in linear] for r, _ in rows], float)
    Q = np.array([[r.get(k, 0) ** 2 / 100 for k in squared] for r, _ in rows], float).reshape(len(rows), len(squared))
    return np.c_[X, Q, np.ones(len(rows)), -np.ones(len(rows))]


def _fit(rows, linear, squared):
    """{rating: [w, q]}, intercept. The intercept is free: two columns of ±1 under the non-negative fit."""
    y = np.array([g for _, g in rows], float)
    coef, _ = nnls(_design(rows, linear, squared), y, maxiter=20000)
    terms = {k: [float(coef[i]), 0.0] for i, k in enumerate(linear)}
    for j, k in enumerate(squared):
        terms.setdefault(k, [0.0, 0.0])[1] = float(coef[len(linear) + j])
    return terms, float(coef[-2] - coef[-1])


def _value(terms, intercept, ratings):
    return intercept + sum(w * ratings.get(k, 0) + q * ratings.get(k, 0) ** 2 / 100 for k, (w, q) in terms.items())


def _cross_validate(rows, linear, squared):
    """(share exact, share within ±1), seeded so a refit reports the same numbers."""
    idx = np.random.default_rng(1).permutation(len(rows))
    errors = []
    for k in range(FOLDS):
        test = set(idx[k::FOLDS].tolist())
        train = [r for i, r in enumerate(rows) if i not in test]
        if len(train) < 2 or not test:
            continue
        terms, b = _fit(train, linear, squared)
        errors += [abs(round(_value(terms, b, rows[i][0])) - rows[i][1]) for i in test]
    e = np.array(errors)
    return round(float((e == 0).mean()), 3), round(float((e <= 1).mean()), 3)


def fit_archetype(rows, features):
    """The better of linear and squared-term models for one archetype's grades, by cross-validation (within ±1, then
    exact; linear wins a tie)."""
    full, _ = _fit(rows, features, [])
    support = [k for k, (w, _) in full.items() if w > SUPPORT] or features
    lin_cv = _cross_validate(rows, features, [])
    quad_cv = _cross_validate(rows, support, support)
    if (quad_cv[1], quad_cv[0]) > (lin_cv[1], lin_cv[0]):
        terms, b = _fit(rows, support, support)
        model, cv = "quadratic", quad_cv
    else:
        terms, b = full, _fit(rows, features, [])[1]
        model, cv = "linear", lin_cv
    terms = {k: [round(w, 6), round(q, 6)] for k, (w, q) in terms.items() if w > 1e-6 or q > 1e-6}
    # A rating nearly everyone in the archetype has low (a QB's Long Snap: 10 for most, 0-6 for some, 91 for a legend)
    # can still carry information as a marker, but it isn't the archetype's skill, so adjust_ratings leaves it alone.
    median = {k: float(np.median([r.get(k, 0) for r, _ in rows])) for k in terms}
    skills = [k for k in terms if median[k] >= SKILL_MEDIAN]
    return {"model": model, "intercept": round(b, 6), "skills": skills,
            "terms": dict(sorted(terms.items(), key=lambda kv: -(kv[1][0] + kv[1][1] * 0.8))),
            "cv_exact": cv[0], "cv_within_1": cv[1], "linear_cv_within_1": lin_cv[1]}


def usable_players(snapshot):
    # Players whose archetype grades disagree with overall (e.g. Carter Landry, a converted
    # WR whose stored grades stayed flat at 83 while overall was 91) have stale grades that distort the fit.
    return [p for p in snapshot["players"]
            if p.get("ratings")
            and max(p.get("archetype_grades") or [0]) > 0
            and not (p.get("position") == "QB" and max(p.get("archetype_grades") or [0]) != p.get("overall"))]


def fit_weights(snapshot):
    """The weights document: the slot map, one model per archetype, and how often max(predicted grades) is the OVR."""
    players = usable_players(snapshot)
    features = sorted({k for p in players for k in p["ratings"]})
    slots = slot_map(players)
    samples = grade_samples(players, slots)
    positions = defaultdict(set)
    for pos, archs in slots.items():
        for a in archs:
            if a:
                positions[a].add(pos)
    archetypes = {}
    for arch, rows in sorted(samples.items()):
        # QB formulas in Madden do not use LongSnap; in sample saves with few QBs, correlation with age/status
        # can tempt NNLS to fit spurious LongSnap terms that blow up on real/scanned players.
        arch_features = [k for k in features if k != "LongSnap"] if arch.startswith("QB_") else features
        archetypes[arch] = {"positions": sorted(positions[arch]), "n": len(rows), **fit_archetype(rows, arch_features)}
    n = sum(a["n"] for a in archetypes.values())
    doc = {
        "game_year": snapshot["meta"]["game_year"],
        "source": os.path.basename(snapshot["meta"].get("source", "")),
        "players": len(players),
        "samples": n,
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cv_exact": round(sum(a["cv_exact"] * a["n"] for a in archetypes.values()) / n, 3),
        "cv_within_1": round(sum(a["cv_within_1"] * a["n"] for a in archetypes.values()) / n, 3),
        "slots": slots,
        "archetypes": archetypes,
    }
    hits = [predict_ovr(doc, p["position"], p["ratings"])[0] - p["overall"] for p in players if p["position"] in slots]
    doc["ovr_exact"] = round(sum(h == 0 for h in hits) / len(hits), 3)
    doc["ovr_within_1"] = round(sum(abs(h) <= 1 for h in hits) / len(hits), 3)
    return doc


# -------------------------------------------------------------------- using ----
def load_weights(path=DEFAULT_PATH):
    with open(path) as fh:
        return json.load(fh)


def predict_grade(doc, archetype, ratings, exact=False):
    """This archetype's OVR for these ratings (a float with exact=True)."""
    a = doc["archetypes"][archetype]
    if archetype.startswith("QB_") and "LongSnap" in ratings and "LongSnap" in a.get("terms", {}):
        ratings = {k: (min(v, 10) if k == "LongSnap" else v) for k, v in ratings.items()}
    value = _value(a["terms"], a["intercept"], ratings)
    return value if exact else int(round(value))


def predict_grades(doc, position, ratings):
    """{archetype: OVR} for every archetype of the position."""
    return {a: predict_grade(doc, a, ratings) for a in doc["slots"][position] if a and a in doc["archetypes"]}


def predict_ovr(doc, position, ratings, current=None):
    """(OVR, archetype): the highest archetype grade. On a tie the current archetype is kept if it's among the tied
    ones (what the save shows), else the first in slot order."""
    grades = predict_grades(doc, position, ratings)
    top = max(grades.values())
    tied = [a for a, g in grades.items() if g == top]
    return top, (current if current in tied else tied[0])


def _gain(terms, k, x, step):
    w, q = terms.get(k, (0.0, 0.0))
    return w * step + q * ((x + step) ** 2 - x ** 2) / 100


def adjust_ratings(doc, archetype, ratings, target, max_steps=600, floor=0, cap=RATING_MAX, skills=None):
    """Ratings moved one point at a time until the archetype's grade is `target`, using only the archetype's skills
    (its formula's ratings with a median of at least SKILL_MEDIAN among its players): up on ratings that raise it most and
    aren't capped, down on those that lower it most and are above the floor. Each step picks the best gain per change
    already made to that rating, so the edit spreads over the ratings that matter instead of maxing one. Returns
    (new ratings, {rating: change}); raises ValueError if the target can't be reached. The player's OVR is the highest
    archetype grade, so check predict_ovr afterwards: another archetype can end up above the target. `skills` overrides
    the ratings it may move (the teleporter's settling step narrows them)."""
    terms = doc["archetypes"][archetype]["terms"]
    if skills is None:
        skills = doc["archetypes"][archetype].get("skills", list(terms))
    out = dict(ratings)
    changes = defaultdict(int)
    for _ in range(max_steps):
        current = predict_grade(doc, archetype, out)
        if current == target:
            return out, {k: v for k, v in changes.items() if v}
        step = 1 if current < target else -1
        options = [k for k in skills if (out.get(k, 0) < cap if step > 0 else out.get(k, 0) > floor)]
        options = [k for k in options if _gain(terms, k, out.get(k, 0), step) != 0]
        if not options:
            break
        best = max(options, key=lambda k: abs(_gain(terms, k, out.get(k, 0), step)) / (1 + abs(changes[k])))
        out[best] = out.get(best, 0) + step
        changes[best] += step
    raise ValueError(f"{archetype}: can't reach {target} from {predict_grade(doc, archetype, ratings)}")


# ---------------------------------------------------------------------- CLI ----
def report(doc):
    lines = [f"{doc['players']} players, {doc['samples']} archetype grades, game year {doc['game_year']} ({doc['source']}): "
             f"grades {doc['cv_exact']:.0%} exact / {doc['cv_within_1']:.0%} within ±1 (cross-validated); "
             f"OVR as the top grade {doc['ovr_exact']:.0%} exact / {doc['ovr_within_1']:.0%} within ±1 (in-sample)"]
    for arch, a in sorted(doc["archetypes"].items(), key=lambda kv: kv[1]["cv_within_1"]):
        top = ", ".join(f"{k} {w:.2f}{f'+{q:.2f}²' if q else ''}" for k, (w, q) in list(a["terms"].items())[:4])
        lines.append(f"  {arch:24} n={a['n']:3} {a['model']:9} ±1 {a['cv_within_1']:.0%} (linear {a['linear_cv_within_1']:.0%})"
                     f"  exact {a['cv_exact']:.0%}  {top}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit", help="fit the formulas from a save-reader snapshot (reader version 2+)")
    f.add_argument("snapshot")
    f.add_argument("--out", default=DEFAULT_PATH)
    s = sub.add_parser("show", help="print a stored weights file's accuracy")
    s.add_argument("--weights", default=DEFAULT_PATH)
    a = ap.parse_args()
    if a.cmd == "fit":
        with open(a.snapshot) as fh:
            snapshot = json.load(fh)
        if snapshot["meta"].get("reader_version", 1) < 2:
            sys.exit("the snapshot has no archetype grades: re-read the save with pc_save_reader.mjs (version 2+)")
        doc = fit_weights(snapshot)
        with open(a.out, "w") as fh:
            json.dump(doc, fh, indent=1)
        print(report(doc))
        print(f"→ {a.out}")
    else:
        print(report(load_weights(a.weights)))


if __name__ == "__main__":
    sys.exit(main())
