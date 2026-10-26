"""
Invented coaching staff (PROJECT_SPEC §2.19): names and backgrounds for the coaches the hiring cycle makes up
(internal promotions, a new head coach's own coordinator, outside hires). Untracked narrative, so invention is fine
(newsroom hallucination policy); names are seeded and never repeat a coach or coordinator already stored.
"""
import random

FIRST = ["Aaron", "Andre", "Ben", "Brandon", "Bryce", "Caleb", "Carl", "Chris", "Cole", "Corey", "Damon", "Dante",
         "Darnell", "Derek", "Devin", "Drew", "Elliot", "Eric", "Ethan", "Gabe", "Garrett", "Grant", "Greg", "Hank",
         "Isaac", "Jalen", "Jamal", "Jared", "Jesse", "Joel", "Jordan", "Julian", "Keith", "Kendall", "Lamar", "Lance",
         "Luke", "Malcolm", "Marcus", "Mario", "Mason", "Miles", "Nolan", "Omar", "Owen", "Paul", "Quentin", "Reggie",
         "Rick", "Ross", "Russ", "Scott", "Seth", "Terrell", "Travis", "Trent", "Vince", "Wade", "Warren", "Zach"]
LAST = ["Abernathy", "Alvarez", "Banks", "Barlow", "Bishop", "Bledsoe", "Caldwell", "Carrington", "Castellano",
        "Dawkins", "Delgado", "Draper", "Easley", "Fairbanks", "Fenwick", "Garrison", "Gentry", "Haldane", "Hartwell",
        "Holloway", "Ibarra", "Jessup", "Kearney", "Kessler", "Langford", "Lockett", "Maddox", "Merriweather",
        "Montague", "Nakamura", "Norwood", "Oduya", "Pembroke", "Prescott", "Quarles", "Radford", "Rinaldi",
        "Rowland", "Sandoval", "Sterling", "Strickland", "Tate", "Thornton", "Underwood", "Vance", "Waverly",
        "Whitaker", "Yancey", "Youngblood", "Zielinski"]

PROMOTED_FROM = {"OC": ["quarterbacks coach", "passing game coordinator", "run game coordinator",
                        "offensive line coach", "wide receivers coach", "tight ends coach"],
                 "DC": ["linebackers coach", "defensive line coach", "secondary coach",
                        "defensive passing game coordinator", "defensive backs coach"]}
OUTSIDE_FROM = {"HC": ["a college head coach", "a longtime NFL assistant head coach", "a college offensive coordinator",
                       "a former NFL special teams coordinator", "a college defensive coordinator"],
                "OC": ["a college offensive coordinator", "a veteran NFL quarterbacks coach",
                       "a college head coach", "an NFL passing game coordinator"],
                "DC": ["a college defensive coordinator", "a veteran NFL linebackers coach",
                       "an NFL defensive passing game coordinator", "a college head coach"]}


def taken_names(cur):
    cur.execute("SELECT lower(coach_name) FROM coaches UNION SELECT lower(coordinator_name) FROM coordinators")
    return {r[0] for r in cur.fetchall()}


def new_name(seed, taken):
    """A seeded full name not in `taken` (lower-case names); adds it to `taken`."""
    rng = random.Random(f"staffname:{seed}")
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name.lower() not in taken:
            taken.add(name.lower())
            return name


def background(seed, kind, role):
    """'quarterbacks coach' (kind PROMOTED) or 'a college offensive coordinator' (kind OUTSIDE) for `role`."""
    pool = PROMOTED_FROM[role] if kind == "PROMOTED" else OUTSIDE_FROM[role]
    return random.Random(f"staffbg:{seed}").choice(pool)
