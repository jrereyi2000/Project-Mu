import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PIL import Image
import io

from headshots.local.donors import (
    DEFAULT_DONOR_DIR,
    REGISTRY_PATH,
    disqualify_donor,
    load_donor_registry,
    normalize_team,
    save_donor_registry,
)
from headshots.local.build_donor_library import pad_headshot_to_1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply_decisions")

DECISIONS_PAYLOAD = {
  "timestamp": "2026-10-30T10:09:38.857Z",
  "total_swaps": 60,
  "decisions": [
    {
      "action": "swap",
      "team": "CAR",
      "tier": "tier4_edge",
      "old_player": "Mitchell Evans",
      "old_id": "4683243",
      "new_player": "Jaelan Phillips",
      "new_id": "4242975",
      "new_weight": 266,
      "new_pos": "LB"
    },
    {
      "action": "swap",
      "team": "CAR",
      "tier": "tier1_speed",
      "old_player": "Chau Smith-Wade",
      "old_id": "4697636",
      "new_player": "Trevor Etienne",
      "new_id": "4685350",
      "new_weight": 198,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "KC",
      "tier": "tier1_speed",
      "old_player": "Xavier Worthy",
      "old_id": "4683062",
      "new_player": "Cyrus Allen",
      "new_id": "4912218",
      "new_weight": 180,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "NO",
      "tier": "tier2_skill",
      "old_player": "Bryce Lance",
      "old_id": "4879276",
      "new_player": "Kendre Miller",
      "new_id": "4599739",
      "new_weight": 220,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "NO",
      "tier": "tier4_edge",
      "old_player": "Cameron Jordan",
      "old_id": "13971",
      "new_player": "Tyree Wilson",
      "new_id": "4372533",
      "new_weight": 263,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "ATL",
      "tier": "tier1_speed",
      "old_player": "Avieon Terrell",
      "old_id": "4870988",
      "new_player": "Jahan Dotson",
      "new_id": "4361409",
      "new_weight": 184,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "BAL",
      "tier": "tier1_speed",
      "old_player": "Gage Larvadain",
      "old_id": "4877203",
      "new_player": "Keyon Martin",
      "new_id": "4694334",
      "new_weight": 170,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "BUF",
      "tier": "tier2_skill",
      "old_player": "Keon Coleman",
      "old_id": "4635008",
      "new_player": "Ray Davis",
      "new_id": "4429501",
      "new_weight": 211,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "CHI",
      "tier": "tier2_skill",
      "old_player": "Luther Burden III",
      "old_id": "4685278",
      "new_player": "Rome Odunze",
      "new_id": "4431299",
      "new_weight": 214,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "CHI",
      "tier": "tier1_speed",
      "old_player": "Scotty Miller",
      "old_id": "3914397",
      "new_player": "Josh Blackwell",
      "new_id": "4240459",
      "new_weight": 179,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "CHI",
      "tier": "tier4_edge",
      "old_player": "Marcus Davenport",
      "old_id": "3124058",
      "new_player": "Dayo Odeyingbo",
      "new_id": "4242659",
      "new_weight": 282,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "CHI",
      "tier": "tier5_lineman",
      "old_player": "Ozzy Trapilo",
      "old_id": "4432595",
      "new_player": "Neville Gallimore",
      "new_id": "3892883",
      "new_weight": 307,
      "new_pos": "DT"
    },
    {
      "action": "swap",
      "team": "CIN",
      "tier": "tier1_speed",
      "old_player": "Bralyn Lux",
      "old_id": "4575559",
      "new_player": "DJ Turner II",
      "new_id": "4572036",
      "new_weight": 185,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "CIN",
      "tier": "tier3_power",
      "old_player": "Demetrius Knight Jr.",
      "old_id": "4427729",
      "new_player": "Mike Gesicki",
      "new_id": "3116164",
      "new_weight": 245,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "CIN",
      "tier": "tier5_lineman",
      "old_player": "Jacob Bayer",
      "old_id": "4879478",
      "new_player": "Ted Karras",
      "new_id": "2576188",
      "new_weight": 310,
      "new_pos": "C"
    },
    {
      "action": "swap",
      "team": "CIN",
      "tier": "tier4_edge",
      "old_player": "Cedric Johnson",
      "old_id": "4430065",
      "new_player": "Shemar Stewart",
      "new_id": "4685562",
      "new_weight": 267,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "CLE",
      "tier": "tier1_speed",
      "old_player": "Isaiah Bond",
      "old_id": "4808839",
      "new_player": "Michael Coats Jr.",
      "new_id": "5160391",
      "new_weight": 184,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "CLE",
      "tier": "tier4_edge",
      "old_player": "John FitzPatrick",
      "old_id": "4379401",
      "new_player": "Adin Huntington",
      "new_id": "4605792",
      "new_weight": 281,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "CLE",
      "tier": "tier5_lineman",
      "old_player": "Mason Graham",
      "old_id": "4873232",
      "new_player": "Maliek Collins",
      "new_id": "3040471",
      "new_weight": 310,
      "new_pos": "DT"
    },
    {
      "action": "swap",
      "team": "DAL",
      "tier": "tier1_speed",
      "old_player": "Tyrek Funderburk",
      "old_id": "4367330",
      "new_player": "Joey Porter Jr.",
      "new_id": "4426506",
      "new_weight": 194,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "DAL",
      "tier": "tier3_power",
      "old_player": "Jaishawn Barham",
      "old_id": "4685266",
      "new_player": "Michael Trigg",
      "new_id": "4594749",
      "new_weight": 240,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "DAL",
      "tier": "tier5_lineman",
      "old_player": "Marcellus Johnson",
      "old_id": "4363547",
      "new_player": "Drew Shelton",
      "new_id": "4685554",
      "new_weight": 313,
      "new_pos": "OT"
    },
    {
      "action": "swap",
      "team": "DET",
      "tier": "tier4_edge",
      "old_player": "Payton Turner",
      "old_id": "4240269",
      "new_player": "Aidan Hutchinson",
      "new_id": "4372099",
      "new_weight": 268,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "DET",
      "tier": "tier5_lineman",
      "old_player": "Alim McNeill",
      "old_id": "4361662",
      "new_player": "Seth McLaughlin",
      "new_id": "4429187",
      "new_weight": 304,
      "new_pos": "C"
    },
    {
      "action": "swap",
      "team": "DET",
      "tier": "tier1_speed",
      "old_player": "Keith Abney II",
      "old_id": "5093004",
      "new_player": "Dominic Lovett",
      "new_id": "4596439",
      "new_weight": 185,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "GB",
      "tier": "tier1_speed",
      "old_player": "Coleman Owen",
      "old_id": "4578436",
      "new_player": "Jayden Reed",
      "new_id": "4362249",
      "new_weight": 187,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "GB",
      "tier": "tier2_skill",
      "old_player": "Christian Watson",
      "old_id": "4248528",
      "new_player": "Pierre Strong Jr.",
      "new_id": "4249836",
      "new_weight": 207,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "HOU",
      "tier": "tier1_speed",
      "old_player": "Kamari Lassiter",
      "old_id": "4602699",
      "new_player": "Ja'Marcus Ingram",
      "new_id": "4240059",
      "new_weight": 190,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "HOU",
      "tier": "tier2_skill",
      "old_player": "Jayden Higgins",
      "old_id": "4877706",
      "new_player": "Woody Marks",
      "new_id": "4429059",
      "new_weight": 208,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "IND",
      "tier": "tier2_skill",
      "old_player": "Keenan Allen",
      "old_id": "15818",
      "new_player": "Riley Leonard",
      "new_id": "4683423",
      "new_weight": 213,
      "new_pos": "QB"
    },
    {
      "action": "swap",
      "team": "IND",
      "tier": "tier5_lineman",
      "old_player": "Luke Tenuta",
      "old_id": "4361979",
      "new_player": "Tim Smith",
      "new_id": "4692033",
      "new_weight": 306,
      "new_pos": "DT"
    },
    {
      "action": "swap",
      "team": "JAX",
      "tier": "tier1_speed",
      "old_player": "Jabbar Muhammad",
      "old_id": "4432440",
      "new_player": "Austin Trammell",
      "new_id": "4244856",
      "new_weight": 185,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "JAX",
      "tier": "tier2_skill",
      "old_player": "Kaevon Merriweather",
      "old_id": "4360502",
      "new_player": "Quinn Ewers",
      "new_id": "4889929",
      "new_weight": 209,
      "new_pos": "QB"
    },
    {
      "action": "swap",
      "team": "JAX",
      "tier": "tier5_lineman",
      "old_player": "Jonah Monheim",
      "old_id": "4430481",
      "new_player": "Eli Cox",
      "new_id": "4426981",
      "new_weight": 309,
      "new_pos": "C"
    },
    {
      "action": "swap",
      "team": "LV",
      "tier": "tier1_speed",
      "old_player": "Hezekiah Masses",
      "old_id": "4917354",
      "new_player": "Phillip Dorsett II",
      "new_id": "2579604",
      "new_weight": 185,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "LAR",
      "tier": "tier1_speed",
      "old_player": "Emmanuel Forbes Jr.",
      "old_id": "4429767",
      "new_player": "Brennan Presley",
      "new_id": "4431342",
      "new_weight": 174,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "LAR",
      "tier": "tier3_power",
      "old_player": "Nate Landman",
      "old_id": "4243181",
      "new_player": "Omar Speights",
      "new_id": "4428550",
      "new_weight": 234,
      "new_pos": "LB"
    },
    {
      "action": "swap",
      "team": "MIA",
      "tier": "tier4_edge",
      "old_player": "Rodney McGraw",
      "old_id": "4433974",
      "new_player": "Malik Herring",
      "new_id": "4259547",
      "new_weight": 280,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "MIN",
      "tier": "tier1_speed",
      "old_player": "Jordan Addison",
      "old_id": "4429205",
      "new_player": "Myles Price",
      "new_id": "4430656",
      "new_weight": 183,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "NE",
      "tier": "tier1_speed",
      "old_player": "DeMario Douglas",
      "old_id": "4427095",
      "new_player": "Charles Woods",
      "new_id": "4368113",
      "new_weight": 185,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "NE",
      "tier": "tier5_lineman",
      "old_player": "Christian Barmore",
      "old_id": "4372030",
      "new_player": "Ben Brown",
      "new_id": "4362890",
      "new_weight": 313,
      "new_pos": "C"
    },
    {
      "action": "swap",
      "team": "NYG",
      "tier": "tier1_speed",
      "old_player": "DJ James",
      "old_id": "4429891",
      "new_player": "Dru Phillips",
      "new_id": "4431321",
      "new_weight": 180,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "NYG",
      "tier": "tier2_skill",
      "old_player": "Tyrone Tracy Jr.",
      "old_id": "4360516",
      "new_player": "Cam Skattebo",
      "new_id": "4696981",
      "new_weight": 215,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "NYG",
      "tier": "tier4_edge",
      "old_player": "Dawuane Smoot",
      "old_id": "3042476",
      "new_player": "Theo Johnson",
      "new_id": "4429148",
      "new_weight": 264,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "NYG",
      "tier": "tier5_lineman",
      "old_player": "Darius Alexander",
      "old_id": "4426542",
      "new_player": "Bryan Hudson",
      "new_id": "4426489",
      "new_weight": 305,
      "new_pos": "C"
    },
    {
      "action": "swap",
      "team": "NYJ",
      "tier": "tier1_speed",
      "old_player": "Garrett Wilson",
      "old_id": "4569618",
      "new_player": "Caullin Lacy",
      "new_id": "4430185",
      "new_weight": 183,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "NYJ",
      "tier": "tier3_power",
      "old_player": "Demario Davis",
      "old_id": "14958",
      "new_player": "Kenyon Sadiq",
      "new_id": "5083315",
      "new_weight": 241,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "NYJ",
      "tier": "tier4_edge",
      "old_player": "Cody Hardy",
      "old_id": "5086529",
      "new_player": "Braiden McGregor",
      "new_id": "4429005",
      "new_weight": 267,
      "new_pos": "DE"
    },
    {
      "action": "swap",
      "team": "PHI",
      "tier": "tier1_speed",
      "old_player": "DeVonta Smith",
      "old_id": "4241478",
      "new_player": "Britain Covey",
      "new_id": "3926231",
      "new_weight": 173,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "PHI",
      "tier": "tier2_skill",
      "old_player": "Dameon Pierce",
      "old_id": "4360238",
      "new_player": "Dontayvion Wicks",
      "new_id": "4428850",
      "new_weight": 206,
      "new_pos": "WR"
    },
    {
      "action": "swap",
      "team": "PIT",
      "tier": "tier1_speed",
      "old_player": "Roman Wilson",
      "old_id": "4431492",
      "new_player": "Asante Samuel Jr.",
      "new_id": "4363034",
      "new_weight": 180,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "PIT",
      "tier": "tier3_power",
      "old_player": "Robert Tonyan",
      "old_id": "2975674",
      "new_player": "Nick Herbig",
      "new_id": "4429164",
      "new_weight": 240,
      "new_pos": "LB"
    },
    {
      "action": "swap",
      "team": "SEA",
      "tier": "tier2_skill",
      "old_player": "Jake Bobo",
      "old_id": "4360405",
      "new_player": "George Holani",
      "new_id": "4429835",
      "new_weight": 210,
      "new_pos": "RB"
    },
    {
      "action": "swap",
      "team": "SEA",
      "tier": "tier1_speed",
      "old_player": "Rashid Shaheed",
      "old_id": "4032473",
      "new_player": "Avery Smith",
      "new_id": "5082331",
      "new_weight": 185,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "SEA",
      "tier": "tier5_lineman",
      "old_player": "Leonard Williams",
      "old_id": "2971622",
      "new_player": "Logan Brown",
      "new_id": "4570075",
      "new_weight": 312,
      "new_pos": "OT"
    },
    {
      "action": "swap",
      "team": "TB",
      "tier": "tier4_edge",
      "old_player": "Ko Kieft",
      "old_id": "4034779",
      "new_player": "CJ Dippre",
      "new_id": "4596545",
      "new_weight": 256,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "TEN",
      "tier": "tier3_power",
      "old_player": "Anthony Hill Jr.",
      "old_id": "4870805",
      "new_player": "Kylen Granson",
      "new_id": "4039160",
      "new_weight": 242,
      "new_pos": "TE"
    },
    {
      "action": "swap",
      "team": "WAS",
      "tier": "tier1_speed",
      "old_player": "Mike Sainristil",
      "old_id": "4428414",
      "new_player": "Amik Robertson",
      "new_id": "4239694",
      "new_weight": 183,
      "new_pos": "CB"
    },
    {
      "action": "swap",
      "team": "WAS",
      "tier": "tier3_power",
      "old_player": "Sonny Styles",
      "old_id": "5081807",
      "new_player": "Austin Keys",
      "new_id": "4429185",
      "new_weight": 244,
      "new_pos": "LB"
    },
    {
      "action": "swap",
      "team": "WAS",
      "tier": "tier4_edge",
      "old_player": "Charles Omenihu",
      "old_id": "3929865",
      "new_player": "Quentin Moore",
      "new_id": "4875350",
      "new_weight": 260,
      "new_pos": "TE"
    }
  ]
}


def download_donor_template(team: str, tier: str, headshot_url: str, out_dir: Path) -> bool:
    team_dir = out_dir / team
    team_dir.mkdir(parents=True, exist_ok=True)
    base_file = team_dir / f"{tier}_base.png"

    req = urllib.request.Request(headshot_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw_bytes = resp.read()
            padded_im = pad_headshot_to_1024(raw_bytes)
            padded_im.save(base_file, format="PNG")
            return True
    except Exception as e:
        logger.error(f"Failed to download/pad {team} {tier} from {headshot_url}: {e}")
        return False


def main():
    decisions = DECISIONS_PAYLOAD["decisions"]
    logger.info(f"Applying {len(decisions)} user review decisions...")

    successful_swaps = 0
    swapped_items = []

    for d in decisions:
        team = normalize_team(d["team"])
        tier = d["tier"]
        new_id = str(d["new_id"])
        new_player = d["new_player"]
        old_player = d["old_player"]
        reason = f"User review: replaced {old_player} with {new_player}"

        ok = disqualify_donor(team, tier, new_id, reason=reason)
        if ok:
            successful_swaps += 1
            swapped_items.append((team, tier, new_id, new_player))
        else:
            logger.warning(f"Could not swap {team} {tier} to ID {new_id} via registry candidates!")

    logger.info(f"Successfully updated registry for {successful_swaps}/{len(decisions)} items.")

    # Now download new base plates
    registry = load_donor_registry()
    tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for team, tier, new_id, new_player in swapped_items:
            tier_data = registry.get(team, {}).get("tiers", {}).get(tier, {})
            url = tier_data.get("headshot_url")
            if url:
                tasks.append(pool.submit(download_donor_template, team, tier, url, DEFAULT_DONOR_DIR))

        downloaded = sum(1 for t in as_completed(tasks) if t.result())

    logger.info(f"Downloaded and processed {downloaded}/{len(tasks)} new donor base plates onto 1024x1024 canvas.")

if __name__ == "__main__":
    main()
