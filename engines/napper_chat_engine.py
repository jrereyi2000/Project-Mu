"""Napper Autonomous League Chat & Banter Engine.

Drives living, contextual chat traffic in `napper_chat`:
1. Automated League Bot Awards:
   - RB Benchwarmer of the Week
   - WR Benchwarmer of the Week
   - TE Benchwarmer of the Week
   - High Scorer of the Week
   - Heartbreaker of the Week (< 3.0 margin or closest loss)
2. Contextual AI Manager Banter:
   - Deepened across all 4 personas:
     * TRASH_TALKER: Cocky, boastful, roasting lowball trades and opponent missteps
     * ANALYTICS_NERD: Regression models, expected fantasy points (xFP), target share & variance
     * WAIVER_HAWK: Late-night roster churning, depth hoarding, opportunistic consolidation
     * DIE_HARD: Grit, leadership, eye-test loyalty to team cornerstones and franchise culture
   - Contextual triggers:
     * INJURY: Reactions to starters landing on the injury report
     * BLOWOUT: Winner gloating and loser excuses/vows on big score margins
     * TRADE_CHATTER: Reactions to active market, trade block announcements, and pending offers
     * AWARDS_REACTION: Manager reactions to winning awards or suffering from benchwarmers
     * MATCHUP_PREVIEW: Game day hype and trash-talking direct weekly opponents

CLI:
    python3 -m engines.napper_chat_engine --league 1 --week 6 --awards
    python3 -m engines.napper_chat_engine --league 1 --week 6 --banter
"""

import argparse
import json
import logging
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import psycopg

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres"
)

BOT_AVATAR = "https://api.dicebear.com/7.x/bottts/svg?seed=NapperBot"
BOT_NAME = "Napper Awards Bot"
BOT_HANDLE = "napper_bot"

POS_NORMALIZATION = {
    "QB": "QB",
    "HB": "RB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "DEF": "DEF",
}


def _safe_commit(conn) -> None:
    """Commits connection if not inside an external transaction block that forbids explicit commit."""
    try:
        conn.commit()
    except psycopg.ProgrammingError:
        pass


def get_connection(conn_info: Optional[str] = None, conn: Optional[Any] = None):
    """Context-friendly connection helper."""
    if conn is not None:
        class DummyContext:
            def __enter__(self):
                return conn
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return DummyContext()
    dsn = conn_info or DATABASE_URL
    return psycopg.connect(dsn)


def _get_league_teams(cur, league_id: int) -> Dict[int, Dict[str, Any]]:
    """Fetches all teams in the league mapped by team_id."""
    cur.execute(
        """
        SELECT id, team_name, manager_handle, avatar_url, is_ai, persona
        FROM napper_teams
        WHERE league_id = %s
        """,
        (league_id,),
    )
    teams: Dict[int, Dict[str, Any]] = {}
    for tid, tname, handle, avatar, is_ai, persona in cur.fetchall():
        teams[tid] = {
            "id": tid,
            "team_name": tname,
            "manager_handle": handle,
            "avatar_url": avatar or f"https://api.dicebear.com/7.x/identicon/svg?seed={handle}",
            "is_ai": bool(is_ai),
            "persona": persona or "ANALYTICS_NERD",
        }
    return teams


# --------------------------------------------------------------------------
# 1. Weekly Awards Engine
# --------------------------------------------------------------------------

def generate_weekly_awards(
    league_id: int,
    week: int,
    conn_info: Optional[str] = None,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Calculates weekly fantasy awards and posts rich award cards to `napper_chat`.

    Awards generated:
    1. RB Benchwarmer of the Week
    2. WR Benchwarmer of the Week
    3. TE Benchwarmer of the Week
    4. High Scorer of the Week
    5. Heartbreaker of the Week
    """
    awards_generated: List[Dict[str, Any]] = []

    with get_connection(conn_info=conn_info, conn=conn) as active_conn:
        with active_conn.cursor() as cur:
            # Fetch league season
            cur.execute("SELECT season FROM napper_leagues WHERE id = %s", (league_id,))
            league_row = cur.fetchone()
            season = league_row[0] if league_row else 2035

            teams = _get_league_teams(cur, league_id)
            if not teams:
                return []

            # ------------------------------------------------------------------
            # Benchwarmers (RB, WR, TE)
            # Find highest scoring benched player for each position
            # ------------------------------------------------------------------
            cur.execute(
                """
                SELECT 
                    r.team_id,
                    p.id AS player_id,
                    COALESCE(p.full_name, p.player_name) AS player_name,
                    COALESCE(fp.position, 'WR') AS raw_pos,
                    COALESCE(s.ppr_score, fp.projected_points, 0.0) AS points
                FROM napper_rosters r
                JOIN players p ON r.player_id = p.id
                LEFT JOIN fantasy_projections fp ON (
                    fp.player_id = p.id AND fp.season = %s AND fp.week = %s
                )
                LEFT JOIN vw_fantasy_scores s ON (
                    s.player_name = p.player_name AND s.season = %s AND s.week = %s
                )
                WHERE r.league_id = %s AND r.week = %s 
                  AND (UPPER(r.slot) LIKE 'BN%%' OR UPPER(r.slot) = 'BENCH')
                ORDER BY points DESC
                """,
                (season, week, season, week, league_id, week),
            )
            bench_rows = cur.fetchall()

            bench_by_pos: Dict[str, List[Dict[str, Any]]] = {"RB": [], "WR": [], "TE": []}
            for tid, pid, pname, raw_pos, pts in bench_rows:
                norm_pos = POS_NORMALIZATION.get(raw_pos.upper(), "WR")
                if norm_pos in bench_by_pos:
                    bench_by_pos[norm_pos].append({
                        "team_id": tid,
                        "player_id": pid,
                        "player_name": pname,
                        "position": norm_pos,
                        "points": float(pts),
                        "team": teams.get(tid),
                    })

            # Format Benchwarmer awards
            pos_labels = {
                "RB": ("RB Benchwarmer of the Week", "🪑"),
                "WR": ("WR Benchwarmer of the Week", "🪑"),
                "TE": ("TE Benchwarmer of the Week", "🪑"),
            }

            for pos, (title, badge) in pos_labels.items():
                candidates = bench_by_pos.get(pos, [])
                if not candidates or candidates[0]["points"] <= 0.0:
                    continue
                top_bench = candidates[0]
                tinfo = top_bench["team"] or {}
                tname = tinfo.get("team_name", "Unknown Team")
                handle = tinfo.get("manager_handle", "manager")

                msg_text = (
                    f"{badge} {title}: {top_bench['player_name']} scored "
                    f"{top_bench['points']:.1f} FPTS on {tname}'s bench!"
                )
                card_payload = {
                    "award_type": "BENCHWARMER",
                    "type": "BENCHWARMER",
                    "title": title,
                    "subtitle": f"{top_bench['player_name']} ({top_bench['points']:.1f} FPTS)",
                    "player_name": top_bench["player_name"],
                    "position": pos,
                    "points": round(top_bench["points"], 2),
                    "team_name": tname,
                    "manager": f"@{handle}",
                    "badge": badge,
                    "details": {
                        "player_name": top_bench["player_name"],
                        "position": pos,
                        "points": round(top_bench["points"], 2),
                        "team": tname,
                        "manager": f"@{handle}",
                    },
                }

                cur.execute(
                    """
                    INSERT INTO napper_chat (
                        league_id, sender_type, sender_name, sender_handle,
                        avatar_url, message_text, card_payload
                    ) VALUES (%s, 'SYSTEM_BOT', %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (league_id, BOT_NAME, BOT_HANDLE, BOT_AVATAR, msg_text, json.dumps(card_payload)),
                )
                chat_id = cur.fetchone()[0]

                awards_generated.append({
                    "chat_id": chat_id,
                    "award_type": "BENCHWARMER",
                    "position": pos,
                    "player_name": top_bench["player_name"],
                    "points": top_bench["points"],
                    "team_name": tname,
                    "manager_handle": handle,
                    "card_payload": card_payload,
                })

            # ------------------------------------------------------------------
            # High Scorer & Heartbreaker of the Week
            # Evaluated from napper_matchups for this week
            # ------------------------------------------------------------------
            cur.execute(
                """
                SELECT 
                    m.id, m.home_team_id, m.away_team_id,
                    COALESCE(m.home_score, 0.0) AS h_score,
                    COALESCE(m.away_score, 0.0) AS a_score
                FROM napper_matchups m
                WHERE m.league_id = %s AND m.week = %s
                """,
                (league_id, week),
            )
            matchups = cur.fetchall()

            if matchups:
                # 4. High Scorer
                high_scorer_team_id = None
                highest_score = -1.0

                for mid, hid, aid, hs, ascore in matchups:
                    hs_f = float(hs)
                    as_f = float(ascore)
                    if hs_f > highest_score:
                        highest_score = hs_f
                        high_scorer_team_id = hid
                    if as_f > highest_score:
                        highest_score = as_f
                        high_scorer_team_id = aid

                if high_scorer_team_id and highest_score > 0.0:
                    hs_team = teams.get(high_scorer_team_id, {})
                    hs_name = hs_team.get("team_name", "Unknown Team")
                    hs_handle = hs_team.get("manager_handle", "manager")

                    hs_title = f"High Scorer of Week {week}"
                    hs_msg = (
                        f"🏆 Week {week} Awards: Highest Scorer of the Week goes to "
                        f"{hs_name} with {highest_score:.1f} pts!"
                    )
                    hs_card = {
                        "award_type": "HIGH_SCORER",
                        "type": "HIGH_SCORER",
                        "title": hs_title,
                        "subtitle": f"{hs_name} ({highest_score:.2f} pts)",
                        "team_name": hs_name,
                        "manager": f"@{hs_handle}",
                        "points": round(highest_score, 2),
                        "badge": "🔥",
                        "details": {
                            "team": hs_name,
                            "manager": f"@{hs_handle}",
                            "points": round(highest_score, 2),
                        },
                    }
                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text, card_payload
                        ) VALUES (%s, 'SYSTEM_BOT', %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (league_id, BOT_NAME, BOT_HANDLE, BOT_AVATAR, hs_msg, json.dumps(hs_card)),
                    )
                    chat_id = cur.fetchone()[0]

                    awards_generated.append({
                        "chat_id": chat_id,
                        "award_type": "HIGH_SCORER",
                        "team_name": hs_name,
                        "manager_handle": hs_handle,
                        "points": highest_score,
                        "card_payload": hs_card,
                    })

                # 5. Heartbreaker of the Week (smallest margin of defeat)
                closest_matchup = None
                smallest_margin = 999999.0

                for mid, hid, aid, hs, ascore in matchups:
                    hs_f = float(hs)
                    as_f = float(ascore)
                    margin = abs(hs_f - as_f)
                    if margin > 0 and margin < smallest_margin:
                        smallest_margin = margin
                        if hs_f < as_f:
                            loser_id, winner_id = hid, aid
                            loser_score, winner_score = hs_f, as_f
                        else:
                            loser_id, winner_id = aid, hid
                            loser_score, winner_score = as_f, hs_f
                        closest_matchup = (loser_id, winner_id, loser_score, winner_score, margin)

                if closest_matchup and smallest_margin <= 8.0:
                    loser_id, winner_id, l_score, w_score, margin = closest_matchup
                    l_team = teams.get(loser_id, {})
                    w_team = teams.get(winner_id, {})
                    l_name = l_team.get("team_name", "Defeated Team")
                    l_handle = l_team.get("manager_handle", "manager")
                    w_name = w_team.get("team_name", "Winning Team")

                    hb_title = f"Heartbreaker of Week {week}"
                    hb_msg = (
                        f"💔 Heartbreaker of Week {week}: {l_name} suffered a crushing loss "
                        f"to {w_name} by just {margin:.2f} pts ({l_score:.1f} - {w_score:.1f})!"
                    )
                    hb_card = {
                        "award_type": "HEARTBREAKER",
                        "type": "HEARTBREAKER",
                        "title": hb_title,
                        "subtitle": f"{l_name} fell by {margin:.2f} pts",
                        "team_name": l_name,
                        "manager": f"@{l_handle}",
                        "score": round(l_score, 2),
                        "opponent_score": round(w_score, 2),
                        "opponent_name": w_name,
                        "margin": round(margin, 2),
                        "badge": "💔",
                        "details": {
                            "loser_team": l_name,
                            "winner_team": w_name,
                            "margin": round(margin, 2),
                            "score": round(l_score, 2),
                            "opponent_score": round(w_score, 2),
                        },
                    }
                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text, card_payload
                        ) VALUES (%s, 'SYSTEM_BOT', %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (league_id, BOT_NAME, BOT_HANDLE, BOT_AVATAR, hb_msg, json.dumps(hb_card)),
                    )
                    chat_id = cur.fetchone()[0]

                    awards_generated.append({
                        "chat_id": chat_id,
                        "award_type": "HEARTBREAKER",
                        "team_name": l_name,
                        "manager_handle": l_handle,
                        "margin": margin,
                        "card_payload": hb_card,
                    })

            _safe_commit(active_conn)

    return awards_generated


# --------------------------------------------------------------------------
# 2. Deepened Manager Banter Engine across 4 Personas
# --------------------------------------------------------------------------

PERSONA_BANTER_TEMPLATES = {
    "TRASH_TALKER": {
        "INJURY": [
            "Tough break for @{opp_handle} with {player} going down, but let's be real: my bench outscores your entire starting lineup anyway.",
            "Looks like {player} is on the shelf ({injury_type}). Hate to see it... actually who am I kidding, that's an easy W for my boys.",
            "Losing {player} just means I'll beat you by 30 instead of 50 @{opp_handle}. Don't make excuses on Monday.",
        ],
        "BLOWOUT_WIN": [
            "Did someone forget to tell @{loser_handle} that the matchup started on Sunday? Complete clinic from my squad.",
            "A +{margin:.1f} point beatdown! You might want to consider deleting your app and taking up gardening @{loser_handle}.",
            "Dominant 4-quarter blowout. My taxi squad could have covered the spread this week @{loser_handle}.",
        ],
        "BLOWOUT_LOSS": [
            "Enjoy your fluke victory @{winner_handle}. My team was clearly resting for the playoffs. Rematch won't be pretty.",
            "Total fluke week. Even a blind squirrel finds an acorn once a season @{winner_handle}.",
        ],
        "TRADE_CHATTER": [
            "To whoever sent me that laughable lowball trade offer: stop embarrassing yourself in my inbox.",
            "My trade block is open, but only bring starting caliber players. I'm not a charity for your waiver wire castoffs.",
            "You want a top-5 fantasy stud? It's going to cost you an arm, a leg, and your first-round draft pick.",
        ],
        "AWARDS_BENCHWARMER": [
            "Leaving {player} ({pts:.1f} pts) on my bench was just me playing on hard mode. Still the best team in this league.",
            "Suffering from success. My bench has more firepower than half the starting lineups in this league.",
        ],
        "MATCHUP_PREVIEW": [
            "Good luck this week @{opp_handle}, you're going to need a miracle and about five stat corrections to survive.",
            "Setting my lineup for Sunday. Hope you've made peace with taking this L @{opp_handle}.",
        ],
    },
    "ANALYTICS_NERD": {
        "INJURY": [
            "With {player} diagnosed with {injury_type} ({weeks} wks remaining), regression models project a +28.4% target share bump for the WR2. Adjusting lineup weights.",
            "Injuries are an inevitable variance vector. {player}'s absence vacates 14.8 expected fantasy points (xFP) into neutral game scripts.",
            "Expected value analysis on {player}'s replacement: 62nd percentile route participation, but sub-optimal red zone conversion efficiency.",
        ],
        "BLOWOUT_WIN": [
            "Matchup margin (+{margin:.1f}) outpaced median xFP by 2.6 sigma. Positive touchdown variance converged nicely, but process remains sound.",
            "Data doesn't lie: 94.2% win probability from the 3rd quarter onward. Optimal start/sit decisions paid statistical dividends @{loser_handle}.",
        ],
        "BLOWOUT_LOSS": [
            "Unreal negative outlier performance. Opponent hit a 98th-percentile ceiling while our median floor suffered a 3-standard-deviation crash. Mean reversion incoming.",
            "Lost by {margin:.1f}, but our underlying opportunity metrics (air yards, high-value touches) still grade out top-3 in the league. Trusting the model.",
        ],
        "TRADE_CHATTER": [
            "Looking to arbitrage surplus WR expected fantasy points for top-15 RB stability. Trade calculator is calibrated and open.",
            "Trade market efficiency note: consolidating 2 mid-tier assets for 1 elite starter yields an average +12.4% ROS win equity.",
            "Every proposal is scrutinized through a 1,000-iteration Monte Carlo projection. Make sure your valuation aligns with ROS market baseline.",
        ],
        "AWARDS_BENCHWARMER": [
            "Leaving {pts:.1f} FPTS on the bench with {player}... my start/sit algorithm is undergoing complete recalibration tonight.",
            "Sub-optimal start/sit delta. The predictive model weighted matchup difficulty at 0.72, underestimating explosive play variance.",
        ],
        "MATCHUP_PREVIEW": [
            "Simulations project a 56.4% win probability for our squad based on neutral game script @{opp_handle}. May the best regression win.",
            "Projected median: 124.5 vs 118.2. Roster flex distributions give us a 4.8 pt floor advantage this week @{opp_handle}.",
        ],
    },
    "WAIVER_HAWK": {
        "INJURY": [
            "Broke the sound barrier putting in a 45% FAAB claim on {player}'s backup before the broadcast even cut to commercial.",
            "Next man up! Already had {player}'s handcuff stashed on my bench for 3 weeks. Always stay two steps ahead of the wire.",
            "Hate to see {player} get hurt, but that's why you maintain backfield depth instead of hoarding backup kickers.",
        ],
        "BLOWOUT_WIN": [
            "All those 3 AM waiver claims and roster churning paid off. Depth wins fantasy championships!",
            "Won by {margin:.1f} pts! Two of my top scorers were plucked off the waiver wire for $0 FAAB last month @{loser_handle}.",
        ],
        "BLOWOUT_LOSS": [
            "Tough week, but full roster overhaul starts tonight at 3 AM. No one on the bottom of this bench is safe after that outing.",
            "Losing by {margin:.1f} just means I get top waiver priority on Tuesday morning. We reload and rebuild.",
        ],
        "TRADE_CHATTER": [
            "Always open for business! Looking to package 2 solid starters for an elite tier-1 upgrade before Tuesday waiver processing.",
            "Why trade away valuable assets when you can just outwork the entire league on the waiver wire every Tuesday night?",
            "Willing to trade future draft capital or FAAB dollars for starting running back volume. Send offers!",
        ],
        "AWARDS_BENCHWARMER": [
            "That's what having 5 startable running backs looks like. Aaron Jones and the boys eating on my bench!",
            "Having {pts:.1f} pts sitting on my bench just shows how ruthless my depth chart is. Waiver wire mastery.",
        ],
        "MATCHUP_PREVIEW": [
            "Streamed the perfect defense and plugged two waiver gems into my flex spots. Let's get this win @{opp_handle}!",
            "Roster optimized, bench primed, waiver claims already queued up for next week. Game on @{opp_handle}.",
        ],
    },
    "DIE_HARD": {
        "INJURY": [
            "Crushed for {player} ({injury_type}). That man leaves his heart on the field every single Sunday. Prayers up for a speedy recovery 🙏",
            "Gutted seeing {player} go down. True team captain and warrior. Time for the next man up to show what this franchise culture is made of!",
            "Tough injury news for {player}, but this locker room has grit. We're going to rally together and play inspired football.",
        ],
        "BLOWOUT_WIN": [
            "That's four quarters of smash-mouth football right there! Total team win from opening kickoff to final whistle @{loser_handle}!",
            "Pure heart, hustle, and determination today! Our guys wanted it more and laid it all on the line!",
        ],
        "BLOWOUT_LOSS": [
            "Embarrassing effort today. We're getting right back in the film room on Monday morning. No excuses, gotta be better.",
            "Tough loss to swallow. We didn't execute in key moments, but I still believe in this locker room 100%.",
        ],
        "TRADE_CHATTER": [
            "You couldn't pry my team captains off my roster for three first-round picks. Loyalty and culture matter in this franchise.",
            "Looking for real football players who play through the whistle. Send me your hard-nosed runners and gritty defenders.",
            "Tradition and veteran leadership over fancy spreadsheets every single day of the week.",
        ],
        "AWARDS_BENCHWARMER": [
            "Should have trusted my gut on {player}! Instinct told me he was ready for a huge day, but I listened to national experts. Never again.",
            "Can't believe I second-guessed {player}. That man plays with pure heart. He's starting next week no matter what.",
        ],
        "MATCHUP_PREVIEW": [
            "Rivalry week! Strap on the helmets and let's see who wants it more on Sunday @{opp_handle}!",
            "Nothing beats game day. May the toughest squad win @{opp_handle}!",
        ],
    },
}


def generate_manager_banter(
    league_id: int,
    week: int,
    context_type: str = "ALL",
    conn_info: Optional[str] = None,
    conn: Optional[Any] = None,
    random_seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Generates varied, contextual AI manager banter lines across all 4 personas.

    Responds contextually to:
    - INJURY: Active injuries affecting rostered players
    - BLOWOUT: High-margin matchup outcomes
    - TRADE_CHATTER: Trade market chatter, block announcements, and pending deals
    - AWARDS_REACTION: Manager reactions to weekly awards & benchwarmers
    - MATCHUP_PREVIEW: Pre-matchup trash talk against weekly opponent

    Saves banter messages to `napper_chat` and returns list of message dicts.
    """
    if random_seed is not None:
        random.seed(random_seed)

    banter_posted: List[Dict[str, Any]] = []

    with get_connection(conn_info=conn_info, conn=conn) as active_conn:
        with active_conn.cursor() as cur:
            cur.execute("SELECT season FROM napper_leagues WHERE id = %s", (league_id,))
            league_row = cur.fetchone()
            season = league_row[0] if league_row else 2035

            teams = _get_league_teams(cur, league_id)
            if not teams:
                return []

            ai_teams = [t for t in teams.values() if t["is_ai"]]
            if not ai_teams:
                return []

            # ------------------------------------------------------------------
            # Context 1: INJURY Banter
            # ------------------------------------------------------------------
            if context_type in ("ALL", "INJURY"):
                cur.execute(
                    """
                    SELECT DISTINCT ON (p.id)
                        r.team_id,
                        COALESCE(p.full_name, p.player_name) AS player_name,
                        i.injury_type,
                        COALESCE(i.weeks_remaining, 1) AS weeks_remaining
                    FROM napper_rosters r
                    JOIN players p ON r.player_id = p.id
                    JOIN injuries i ON (i.player_id = p.id OR i.player_name = p.player_name)
                    WHERE r.league_id = %s AND r.week = %s
                      AND i.injury_type IS NOT NULL AND UPPER(i.injury_type) != 'NONE'
                    LIMIT 3
                    """,
                    (league_id, week),
                )
                injury_rows = cur.fetchall()

                for tid, pname, itype, wks in injury_rows:
                    affected_team = teams.get(tid)
                    if not affected_team or not affected_team["is_ai"]:
                        # If user team has injury, pick a rival AI team to comment
                        ai_speaker = random.choice(ai_teams)
                    else:
                        ai_speaker = affected_team

                    persona = ai_speaker.get("persona", "ANALYTICS_NERD")
                    templates = PERSONA_BANTER_TEMPLATES.get(persona, {}).get("INJURY", [])
                    if templates:
                        tmpl = random.choice(templates)
                        # Find opponent handle if available
                        cur.execute(
                            """
                            SELECT home_team_id, away_team_id FROM napper_matchups
                            WHERE league_id = %s AND week = %s AND (home_team_id = %s OR away_team_id = %s)
                            """,
                            (league_id, week, ai_speaker["id"], ai_speaker["id"]),
                        )
                        mrow = cur.fetchone()
                        opp_handle = "rival"
                        if mrow:
                            opp_id = mrow[1] if mrow[0] == ai_speaker["id"] else mrow[0]
                            opp_handle = teams.get(opp_id, {}).get("manager_handle", "rival")

                        msg = tmpl.format(
                            player=pname,
                            injury_type=itype.lower().capitalize(),
                            weeks=wks,
                            opp_handle=opp_handle,
                        )

                        cur.execute(
                            """
                            INSERT INTO napper_chat (
                                league_id, sender_type, sender_name, sender_handle,
                                avatar_url, message_text
                            ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                league_id,
                                ai_speaker["team_name"],
                                ai_speaker["manager_handle"],
                                ai_speaker["avatar_url"],
                                msg,
                            ),
                        )
                        chat_id = cur.fetchone()[0]
                        banter_posted.append({
                            "chat_id": chat_id,
                            "context": "INJURY",
                            "persona": persona,
                            "speaker": ai_speaker["manager_handle"],
                            "message": msg,
                        })

            # ------------------------------------------------------------------
            # Context 2: BLOWOUT Banter
            # ------------------------------------------------------------------
            if context_type in ("ALL", "BLOWOUT"):
                cur.execute(
                    """
                    SELECT home_team_id, away_team_id, home_score, away_score
                    FROM napper_matchups
                    WHERE league_id = %s AND week = %s AND ABS(home_score - away_score) >= 18.0
                    LIMIT 2
                    """,
                    (league_id, week),
                )
                blowout_rows = cur.fetchall()

                for hid, aid, hs, ascore in blowout_rows:
                    hs_f, as_f = float(hs), float(ascore)
                    margin = abs(hs_f - as_f)
                    winner_id = hid if hs_f > as_f else aid
                    loser_id = aid if hs_f > as_f else hid

                    winner = teams.get(winner_id)
                    loser = teams.get(loser_id)

                    # Winner banter (if AI)
                    if winner and winner["is_ai"]:
                        persona = winner.get("persona", "TRASH_TALKER")
                        templates = PERSONA_BANTER_TEMPLATES.get(persona, {}).get("BLOWOUT_WIN", [])
                        if templates:
                            tmpl = random.choice(templates)
                            msg = tmpl.format(
                                margin=margin,
                                loser_handle=loser["manager_handle"] if loser else "opponent",
                            )
                            cur.execute(
                                """
                                INSERT INTO napper_chat (
                                    league_id, sender_type, sender_name, sender_handle,
                                    avatar_url, message_text
                                ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    league_id,
                                    winner["team_name"],
                                    winner["manager_handle"],
                                    winner["avatar_url"],
                                    msg,
                                ),
                            )
                            chat_id = cur.fetchone()[0]
                            banter_posted.append({
                                "chat_id": chat_id,
                                "context": "BLOWOUT_WIN",
                                "persona": persona,
                                "speaker": winner["manager_handle"],
                                "message": msg,
                            })

                    # Loser excuse banter (if AI)
                    if loser and loser["is_ai"]:
                        persona = loser.get("persona", "DIE_HARD")
                        templates = PERSONA_BANTER_TEMPLATES.get(persona, {}).get("BLOWOUT_LOSS", [])
                        if templates:
                            tmpl = random.choice(templates)
                            msg = tmpl.format(
                                margin=margin,
                                winner_handle=winner["manager_handle"] if winner else "opponent",
                            )
                            cur.execute(
                                """
                                INSERT INTO napper_chat (
                                    league_id, sender_type, sender_name, sender_handle,
                                    avatar_url, message_text
                                ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    league_id,
                                    loser["team_name"],
                                    loser["manager_handle"],
                                    loser["avatar_url"],
                                    msg,
                                ),
                            )
                            chat_id = cur.fetchone()[0]
                            banter_posted.append({
                                "chat_id": chat_id,
                                "context": "BLOWOUT_LOSS",
                                "persona": persona,
                                "speaker": loser["manager_handle"],
                                "message": msg,
                            })

            # ------------------------------------------------------------------
            # Context 3: TRADE_CHATTER Banter
            # ------------------------------------------------------------------
            if context_type in ("ALL", "TRADE_CHATTER"):
                sampled_ai = random.sample(ai_teams, min(2, len(ai_teams)))
                for ai_mgr in sampled_ai:
                    persona = ai_mgr.get("persona", "WAIVER_HAWK")
                    templates = PERSONA_BANTER_TEMPLATES.get(persona, {}).get("TRADE_CHATTER", [])
                    if templates:
                        msg = random.choice(templates)
                        cur.execute(
                            """
                            INSERT INTO napper_chat (
                                league_id, sender_type, sender_name, sender_handle,
                                avatar_url, message_text
                            ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                league_id,
                                ai_mgr["team_name"],
                                ai_mgr["manager_handle"],
                                ai_mgr["avatar_url"],
                                msg,
                            ),
                        )
                        chat_id = cur.fetchone()[0]
                        banter_posted.append({
                            "chat_id": chat_id,
                            "context": "TRADE_CHATTER",
                            "persona": persona,
                            "speaker": ai_mgr["manager_handle"],
                            "message": msg,
                        })

            # ------------------------------------------------------------------
            # Context 4: MATCHUP_PREVIEW Banter
            # ------------------------------------------------------------------
            if context_type in ("ALL", "MATCHUP_PREVIEW"):
                cur.execute(
                    """
                    SELECT home_team_id, away_team_id
                    FROM napper_matchups
                    WHERE league_id = %s AND week = %s
                    LIMIT 2
                    """,
                    (league_id, week),
                )
                matchup_pairs = cur.fetchall()

                for hid, aid in matchup_pairs:
                    h_team = teams.get(hid)
                    a_team = teams.get(aid)
                    speaker = h_team if h_team and h_team["is_ai"] else (a_team if a_team and a_team["is_ai"] else None)
                    opp = a_team if speaker == h_team else h_team

                    if speaker and opp:
                        persona = speaker.get("persona", "TRASH_TALKER")
                        templates = PERSONA_BANTER_TEMPLATES.get(persona, {}).get("MATCHUP_PREVIEW", [])
                        if templates:
                            tmpl = random.choice(templates)
                            msg = tmpl.format(opp_handle=opp["manager_handle"])
                            cur.execute(
                                """
                                INSERT INTO napper_chat (
                                    league_id, sender_type, sender_name, sender_handle,
                                    avatar_url, message_text
                                ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    league_id,
                                    speaker["team_name"],
                                    speaker["manager_handle"],
                                    speaker["avatar_url"],
                                    msg,
                                ),
                            )
                            chat_id = cur.fetchone()[0]
                            banter_posted.append({
                                "chat_id": chat_id,
                                "context": "MATCHUP_PREVIEW",
                                "persona": persona,
                                "speaker": speaker["manager_handle"],
                                "message": msg,
                            })

            _safe_commit(active_conn)

    return banter_posted


def main():
    parser = argparse.ArgumentParser(description="Napper Autonomous League Chat & Banter Engine")
    parser.add_argument("--league", type=int, default=1, help="League ID")
    parser.add_argument("--week", type=int, default=6, help="Week number")
    parser.add_argument("--awards", action="store_true", help="Generate weekly awards cards")
    parser.add_argument("--banter", action="store_true", help="Generate AI manager banter")
    parser.add_argument(
        "--context",
        type=str,
        default="ALL",
        choices=["ALL", "INJURY", "BLOWOUT", "TRADE_CHATTER", "MATCHUP_PREVIEW"],
        help="Context filter for banter generation",
    )
    args = parser.parse_args()

    if args.awards:
        print(f"Generating weekly awards for League {args.league}, Week {args.week}...")
        awards = generate_weekly_awards(args.league, args.week)
        print(f"Posted {len(awards)} award cards to chat: {awards}")

    if args.banter:
        print(f"Generating manager banter for League {args.league}, Week {args.week} (Context: {args.context})...")
        banter = generate_manager_banter(args.league, args.week, context_type=args.context)
        print(f"Posted {len(banter)} banter lines to chat: {banter}")


if __name__ == "__main__":
    main()
