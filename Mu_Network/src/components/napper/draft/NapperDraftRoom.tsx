import React, { useState, useEffect, useRef, useCallback } from "react";
import type {
  NapperDraftState,
  NapperDraftPlayer,
  NapperDraftPick,
  NapperDraftTeam,
  NapperDraftComment,
} from "../../../types/napper";
import { API_BASE } from "../../../api";
import DraftBoardGrid from "./DraftBoardGrid";
import DraftPlayerPool from "./DraftPlayerPool";
import DraftRosterQueue from "./DraftRosterQueue";
import DraftRecapModal from "./DraftRecapModal";
import ArchetypeCompositionModal, { ARCHETYPE_PRESETS } from "./ArchetypeCompositionModal";
import { DraftOrderModal } from "./DraftOrderModal";
import { ARCHETYPE_METAS } from "../../../types/napper";
import "./napperDraft.css";

// Comprehensive Mock Player Dataset based on Season 2035 & NFL Consensus ADP
const MOCK_DRAFT_PLAYERS: NapperDraftPlayer[] = [
  {
    player_id: 80,
    name: "Trey'Mane Davis",
    position: "WR",
    team: "CAR",
    bye: 6,
    age: 24.0,
    ros_pct: 99.8,
    adp: 1.0,
    adp_rank: 1,
    proj_pts: 288.0,
    proj_avg: 16.9,
    rush_att: 0,
    rush_yds: 0,
    rush_td: 0,
    rec: 107.0,
    rec_yds: 2111.0,
    rec_td: 29.0,
    headshot_url: "/static/players/80.webp",
    notes: "Consensus #1 overall fantasy pick in season 2035 with 2,111 yards and 29 TDs.",
  },
  {
    player_id: 138,
    name: "KC Concepcion Jr",
    position: "WR",
    team: "CLE",
    bye: 9,
    age: 30.0,
    ros_pct: 99.5,
    adp: 2.0,
    adp_rank: 2,
    proj_pts: 272.0,
    proj_avg: 16.0,
    rush_att: 2.0,
    rush_yds: 12.0,
    rush_td: 0,
    rec: 102.0,
    rec_yds: 1673.0,
    rec_td: 14.0,
    headshot_url: "/static/players/138.webp",
    notes: "Elite route acrobat and target accumulator in Cleveland.",
  },
  {
    player_id: 3,
    name: "G. Crawford",
    position: "QB",
    team: "IND",
    bye: 13,
    age: 26.0,
    ros_pct: 99.4,
    adp: 3.0,
    adp_rank: 3,
    proj_pts: 335.0,
    proj_avg: 19.7,
    rush_att: 95.0,
    rush_yds: 510.0,
    rush_td: 8.0,
    pass_yds: 4200,
    pass_td: 28,
    int: 10,
    headshot_url: "/static/players/3.webp",
    notes: "Top-ranked dual-threat quarterback in season 2035 with immense weekly ceiling.",
  },
  {
    player_id: 1404,
    name: "M. McBride",
    position: "RB",
    team: "DEN",
    bye: 8,
    age: 25.0,
    ros_pct: 99.3,
    adp: 4.0,
    adp_rank: 4,
    proj_pts: 258.0,
    proj_avg: 15.2,
    rush_att: 275.0,
    rush_yds: 1340.0,
    rush_td: 12.0,
    rec: 48.0,
    rec_yds: 390.0,
    rec_td: 2.0,
    headshot_url: "/static/players/1404.webp",
    notes: "Season 2035 workhorse running back commanding true 3-down volume.",
  },
  {
    player_id: 4430807,
    name: "Bijan Robinson",
    position: "RB",
    team: "ATL",
    bye: 11,
    age: 24.7,
    ros_pct: 99.7,
    adp: 1.2,
    adp_rank: 1,
    proj_pts: 260.9,
    proj_avg: 15.3,
    rush_att: 280.0,
    rush_yds: 1372.0,
    rush_td: 9.0,
    rec: 64.0,
    rec_yds: 537.0,
    rec_td: 3.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430807.png",
    notes: "Elite bellcow with unmatched target share and explosive open-field vision.",
  },
  {
    player_id: 4429013,
    name: "Jahmyr Gibbs",
    position: "RB",
    team: "DET",
    bye: 6,
    age: 24.5,
    ros_pct: 99.8,
    adp: 2.7,
    adp_rank: 2,
    proj_pts: 268.4,
    proj_avg: 15.8,
    rush_att: 255.0,
    rush_yds: 1251.0,
    rush_td: 12.0,
    rec: 63.0,
    rec_yds: 533.0,
    rec_td: 3.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4429013.png",
    notes: "Electric dual-threat running back in a top-3 scoring offense.",
  },
  {
    player_id: 4242335,
    name: "Jonathan Taylor",
    position: "RB",
    team: "IND",
    bye: 13,
    age: 27.7,
    ros_pct: 99.7,
    adp: 3.8,
    adp_rank: 3,
    proj_pts: 236.3,
    proj_avg: 13.9,
    rush_att: 287.0,
    rush_yds: 1385.0,
    rush_td: 11.0,
    rec: 36.0,
    rec_yds: 278.0,
    rec_td: 1.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4242335.png",
    notes: "Workhorse back with heavy goal-line volume behind an elite offensive line.",
  },
  {
    player_id: 3117251,
    name: "Christian McCaffrey",
    position: "RB",
    team: "SF",
    bye: 8,
    age: 30.3,
    ros_pct: 99.7,
    adp: 4.9,
    adp_rank: 4,
    proj_pts: 221.0,
    proj_avg: 13.0,
    rush_att: 240.0,
    rush_yds: 984.0,
    rush_td: 7.0,
    rec: 70.0,
    rec_yds: 606.0,
    rec_td: 4.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3117251.png",
    notes: "Premier PPR weapon when healthy, guaranteed red-zone and checkdown target.",
  },
  {
    player_id: 4427366,
    name: "Breece Hall",
    position: "RB",
    team: "NYJ",
    bye: 12,
    age: 25.1,
    ros_pct: 99.6,
    adp: 5.6,
    adp_rank: 5,
    proj_pts: 245.5,
    proj_avg: 14.4,
    rush_att: 260.0,
    rush_yds: 1180.0,
    rush_td: 8.0,
    rec: 68.0,
    rec_yds: 560.0,
    rec_td: 4.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4427366.png",
    notes: "Tremendous breakaway speed and elite receiving profile.",
  },
  {
    player_id: 3929630,
    name: "Saquon Barkley",
    position: "RB",
    team: "PHI",
    bye: 5,
    age: 29.2,
    ros_pct: 99.5,
    adp: 6.4,
    adp_rank: 6,
    proj_pts: 252.0,
    proj_avg: 14.8,
    rush_att: 275.0,
    rush_yds: 1310.0,
    rush_td: 13.0,
    rec: 45.0,
    rec_yds: 340.0,
    rec_td: 2.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3929630.png",
    notes: "Dominant back operating inside Philadelphia's high-efficiency rushing attack.",
  },
  {
    player_id: 4380150,
    name: "CeeDee Lamb",
    position: "WR",
    team: "DAL",
    bye: 7,
    age: 27.2,
    ros_pct: 99.9,
    adp: 7.2,
    adp_rank: 7,
    proj_pts: 278.4,
    proj_avg: 16.4,
    rush_att: 14.0,
    rush_yds: 110.0,
    rush_td: 1.0,
    rec: 112.0,
    rec_yds: 1480.0,
    rec_td: 11.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4380150.png",
    notes: "Alpha WR1 leading the NFL in slot routes and target funnel rate.",
  },
  {
    player_id: 4262921,
    name: "Justin Jefferson",
    position: "WR",
    team: "MIN",
    bye: 6,
    age: 27.0,
    ros_pct: 99.8,
    adp: 8.1,
    adp_rank: 8,
    proj_pts: 272.0,
    proj_avg: 16.0,
    rush_att: 4.0,
    rush_yds: 25.0,
    rush_td: 0.0,
    rec: 104.0,
    rec_yds: 1520.0,
    rec_td: 10.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4262921.png",
    notes: "The gold standard route runner with historic per-game receiving yard averages.",
  },
  {
    player_id: 4362628,
    name: "Ja'Marr Chase",
    position: "WR",
    team: "CIN",
    bye: 12,
    age: 26.3,
    ros_pct: 99.8,
    adp: 9.3,
    adp_rank: 9,
    proj_pts: 266.5,
    proj_avg: 15.7,
    rush_att: 8.0,
    rush_yds: 45.0,
    rush_td: 0.0,
    rec: 98.0,
    rec_yds: 1390.0,
    rec_td: 12.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4362628.png",
    notes: "Top-tier deep threat paired with quarterback Joe Burrow.",
  },
  {
    player_id: 4374302,
    name: "Amon-Ra St. Brown",
    position: "WR",
    team: "DET",
    bye: 6,
    age: 26.8,
    ros_pct: 99.7,
    adp: 10.2,
    adp_rank: 10,
    proj_pts: 258.0,
    proj_avg: 15.2,
    rush_att: 6.0,
    rush_yds: 35.0,
    rush_td: 0.0,
    rec: 106.0,
    rec_yds: 1280.0,
    rec_td: 9.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4374302.png",
    notes: "PPR monster with one of the most secure weekly target floors in football.",
  },
  {
    player_id: 4426515,
    name: "Puka Nacua",
    position: "WR",
    team: "LAR",
    bye: 6,
    age: 25.1,
    ros_pct: 99.4,
    adp: 11.5,
    adp_rank: 11,
    proj_pts: 248.0,
    proj_avg: 14.6,
    rush_att: 12.0,
    rush_yds: 90.0,
    rush_td: 0.0,
    rec: 96.0,
    rec_yds: 1320.0,
    rec_td: 7.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4426515.png",
    notes: "Physical YAC machine heavily targeted in Sean McVay's scheme.",
  },
  {
    player_id: 4258169,
    name: "Nico Collins",
    position: "WR",
    team: "HOU",
    bye: 14,
    age: 27.4,
    ros_pct: 99.3,
    adp: 12.8,
    adp_rank: 12,
    proj_pts: 242.0,
    proj_avg: 14.2,
    rush_att: 2.0,
    rush_yds: 10.0,
    rush_td: 0.0,
    rec: 88.0,
    rec_yds: 1310.0,
    rec_td: 8.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4258169.png",
    notes: "Dominant boundary target averaging over 3.0 yards per route run.",
  },
  {
    player_id: 4432708,
    name: "Marvin Harrison Jr.",
    position: "WR",
    team: "ARI",
    bye: 11,
    age: 24.1,
    ros_pct: 98.9,
    adp: 14.5,
    adp_rank: 13,
    proj_pts: 235.0,
    proj_avg: 13.8,
    rush_att: 3.0,
    rush_yds: 20.0,
    rush_td: 0.0,
    rec: 84.0,
    rec_yds: 1220.0,
    rec_td: 9.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432708.png",
    notes: "Generational size and hands with true top-5 overall receiver ceiling.",
  },
  {
    player_id: 4595348,
    name: "Malik Nabers",
    position: "WR",
    team: "NYG",
    bye: 11,
    age: 23.2,
    ros_pct: 98.7,
    adp: 16.0,
    adp_rank: 14,
    proj_pts: 231.0,
    proj_avg: 13.6,
    rush_att: 6.0,
    rush_yds: 40.0,
    rush_td: 0.0,
    rec: 89.0,
    rec_yds: 1190.0,
    rec_td: 7.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4595348.png",
    notes: "Dynamic open-field separator with extreme target commanding ability.",
  },
  {
    player_id: 4432665,
    name: "Brock Bowers",
    position: "TE",
    team: "LV",
    bye: 10,
    age: 23.6,
    ros_pct: 99.1,
    adp: 17.5,
    adp_rank: 15,
    proj_pts: 218.0,
    proj_avg: 12.8,
    rush_att: 8.0,
    rush_yds: 55.0,
    rush_td: 1.0,
    rec: 88.0,
    rec_yds: 1080.0,
    rec_td: 7.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432665.png",
    notes: "Positional cheat code who lines up across the formation as a receiver.",
  },
  {
    player_id: 4361528,
    name: "Trey McBride",
    position: "TE",
    team: "ARI",
    bye: 11,
    age: 26.8,
    ros_pct: 98.8,
    adp: 19.2,
    adp_rank: 16,
    proj_pts: 205.0,
    proj_avg: 12.1,
    rush_att: 1.0,
    rush_yds: 5.0,
    rush_td: 0.0,
    rec: 85.0,
    rec_yds: 980.0,
    rec_td: 6.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4361528.png",
    notes: "High-volume middle of the field weapon for Kyler Murray.",
  },
  {
    player_id: 3918298,
    name: "Josh Allen",
    position: "QB",
    team: "BUF",
    bye: 12,
    age: 30.1,
    ros_pct: 99.8,
    adp: 21.0,
    adp_rank: 17,
    proj_pts: 345.0,
    proj_avg: 20.3,
    rush_att: 105.0,
    rush_yds: 520.0,
    rush_td: 11.0,
    pass_yds: 4100,
    pass_td: 29,
    int: 12,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3918298.png",
    notes: "The consensus fantasy QB1 offering 10+ rushing touchdowns every single year.",
  },
  {
    player_id: 3916387,
    name: "Lamar Jackson",
    position: "QB",
    team: "BAL",
    bye: 14,
    age: 29.5,
    ros_pct: 99.8,
    adp: 22.8,
    adp_rank: 18,
    proj_pts: 340.0,
    proj_avg: 20.0,
    rush_att: 135.0,
    rush_yds: 780.0,
    rush_td: 6.0,
    pass_yds: 3750,
    pass_td: 26,
    int: 8,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3916387.png",
    notes: "Two-time MVP with unmatched quarterback rushing upside.",
  },
  {
    player_id: 4040715,
    name: "Jalen Hurts",
    position: "QB",
    team: "PHI",
    bye: 5,
    age: 28.1,
    ros_pct: 99.5,
    adp: 25.5,
    adp_rank: 19,
    proj_pts: 325.0,
    proj_avg: 19.1,
    rush_att: 130.0,
    rush_yds: 560.0,
    rush_td: 12.0,
    pass_yds: 3600,
    pass_td: 23,
    int: 10,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4040715.png",
    notes: "Brotherly Shove touchdown machine behind Philadelphia's dominant line.",
  },
  {
    player_id: 3139477,
    name: "Patrick Mahomes",
    position: "QB",
    team: "KC",
    bye: 6,
    age: 31.0,
    ros_pct: 99.7,
    adp: 28.0,
    adp_rank: 20,
    proj_pts: 315.0,
    proj_avg: 18.5,
    rush_att: 65.0,
    rush_yds: 340.0,
    rush_td: 2.0,
    pass_yds: 4400,
    pass_td: 32,
    int: 11,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3139477.png",
    notes: "Masterful playmaker with restored deep passing personnel in Kansas City.",
  },
  {
    player_id: 4430027,
    name: "Sam LaPorta",
    position: "TE",
    team: "DET",
    bye: 6,
    age: 25.4,
    ros_pct: 98.4,
    adp: 30.5,
    adp_rank: 21,
    proj_pts: 188.0,
    proj_avg: 11.1,
    rush_att: 1.0,
    rush_yds: 4.0,
    rush_td: 0.0,
    rec: 74.0,
    rec_yds: 840.0,
    rec_td: 8.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430027.png",
    notes: "Lions red-zone favorite with exceptional hands and route discipline.",
  },
  {
    player_id: 3040151,
    name: "George Kittle",
    position: "TE",
    team: "SF",
    bye: 8,
    age: 32.8,
    ros_pct: 98.0,
    adp: 34.0,
    adp_rank: 22,
    proj_pts: 185.0,
    proj_avg: 10.9,
    rush_att: 2.0,
    rush_yds: 15.0,
    rush_td: 0.0,
    rec: 68.0,
    rec_yds: 920.0,
    rec_td: 6.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3040151.png",
    notes: "Explosive run-after-catch tight end capable of multi-touchdown spike weeks.",
  },
  {
    player_id: 4430878,
    name: "Jaxon Smith-Njigba",
    position: "WR",
    team: "SEA",
    bye: 10,
    age: 24.5,
    ros_pct: 97.5,
    adp: 38.0,
    adp_rank: 23,
    proj_pts: 215.0,
    proj_avg: 12.6,
    rush_att: 3.0,
    rush_yds: 20.0,
    rush_td: 0.0,
    rec: 85.0,
    rec_yds: 1050.0,
    rec_td: 6.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430878.png",
    notes: "Breakout route-running specialist in Ryan Grubb's aggressive passing system.",
  },
  {
    player_id: 4567048,
    name: "Kenneth Walker III",
    position: "RB",
    team: "SEA",
    bye: 10,
    age: 25.8,
    ros_pct: 97.9,
    adp: 41.5,
    adp_rank: 24,
    proj_pts: 212.0,
    proj_avg: 12.5,
    rush_att: 220.0,
    rush_yds: 1050.0,
    rush_td: 9.0,
    rec: 42.0,
    rec_yds: 320.0,
    rec_td: 2.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4567048.png",
    notes: "Home-run hitter with expanded three-down pass game involvement.",
  },
  {
    player_id: 4035538,
    name: "David Montgomery",
    position: "RB",
    team: "DET",
    bye: 6,
    age: 29.1,
    ros_pct: 96.5,
    adp: 45.0,
    adp_rank: 25,
    proj_pts: 198.0,
    proj_avg: 11.6,
    rush_att: 215.0,
    rush_yds: 980.0,
    rush_td: 12.0,
    rec: 25.0,
    rec_yds: 180.0,
    rec_td: 1.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4035538.png",
    notes: "Automatic touchdown machine inside the opponent five-yard line.",
  },
  {
    player_id: 4430737,
    name: "Kyren Williams",
    position: "RB",
    team: "LAR",
    bye: 6,
    age: 26.0,
    ros_pct: 97.0,
    adp: 48.0,
    adp_rank: 26,
    proj_pts: 206.0,
    proj_avg: 12.1,
    rush_att: 225.0,
    rush_yds: 1020.0,
    rush_td: 10.0,
    rec: 34.0,
    rec_yds: 240.0,
    rec_td: 2.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430737.png",
    notes: "High-volume runner trusted in all passing and goal-line situations.",
  },
  {
    player_id: 4259545,
    name: "D'Andre Swift",
    position: "RB",
    team: "CHI",
    bye: 7,
    age: 27.6,
    ros_pct: 95.8,
    adp: 52.0,
    adp_rank: 27,
    proj_pts: 192.0,
    proj_avg: 11.3,
    rush_att: 200.0,
    rush_yds: 910.0,
    rush_td: 6.0,
    rec: 50.0,
    rec_yds: 390.0,
    rec_td: 2.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4259545.png",
    notes: "Versatile space runner with solid receiving floor.",
  },
  {
    player_id: 3042519,
    name: "Aaron Jones",
    position: "RB",
    team: "MIN",
    bye: 6,
    age: 31.8,
    ros_pct: 95.0,
    adp: 56.0,
    adp_rank: 28,
    proj_pts: 186.0,
    proj_avg: 10.9,
    rush_att: 190.0,
    rush_yds: 890.0,
    rush_td: 6.0,
    rec: 44.0,
    rec_yds: 330.0,
    rec_td: 2.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3042519.png",
    notes: "Veteran efficiency leader with strong receiving upside.",
  },
  {
    player_id: 4429022,
    name: "Garrett Wilson",
    position: "WR",
    team: "NYJ",
    bye: 12,
    age: 26.2,
    ros_pct: 98.2,
    adp: 59.0,
    adp_rank: 29,
    proj_pts: 228.0,
    proj_avg: 13.4,
    rush_att: 4.0,
    rush_yds: 25.0,
    rush_td: 0.0,
    rec: 92.0,
    rec_yds: 1180.0,
    rec_td: 7.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4429022.png",
    notes: "Elite route acrobat and target accumulator.",
  },
  {
    player_id: 4372016,
    name: "Jaylen Waddle",
    position: "WR",
    team: "MIA",
    bye: 6,
    age: 27.8,
    ros_pct: 96.0,
    adp: 63.0,
    adp_rank: 30,
    proj_pts: 210.0,
    proj_avg: 12.4,
    rush_att: 3.0,
    rush_yds: 20.0,
    rush_td: 0.0,
    rec: 80.0,
    rec_yds: 1100.0,
    rec_td: 6.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4372016.png",
    notes: "Blazing speed merchant in Mike McDaniel's track-star offense.",
  },
  {
    player_id: 4432770,
    name: "Xavier Worthy",
    position: "WR",
    team: "KC",
    bye: 6,
    age: 23.3,
    ros_pct: 94.5,
    adp: 68.0,
    adp_rank: 31,
    proj_pts: 195.0,
    proj_avg: 11.5,
    rush_att: 10.0,
    rush_yds: 75.0,
    rush_td: 1.0,
    rec: 65.0,
    rec_yds: 950.0,
    rec_td: 7.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432770.png",
    notes: "4.21 combine speedster unleashed on designed deep posts and sweeps.",
  },
  {
    player_id: 4432773,
    name: "Brian Thomas Jr.",
    position: "WR",
    team: "JAX",
    bye: 12,
    age: 23.8,
    ros_pct: 95.0,
    adp: 72.0,
    adp_rank: 32,
    proj_pts: 202.0,
    proj_avg: 11.9,
    rush_att: 2.0,
    rush_yds: 15.0,
    rush_td: 0.0,
    rec: 72.0,
    rec_yds: 1060.0,
    rec_td: 8.0,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432773.png",
    notes: "Trevor Lawrence's big-bodied vertical separator.",
  },
  {
    player_id: 4036378,
    name: "Jordan Love",
    position: "QB",
    team: "GB",
    bye: 10,
    age: 27.9,
    ros_pct: 96.0,
    adp: 76.0,
    adp_rank: 33,
    proj_pts: 298.0,
    proj_avg: 17.5,
    rush_att: 50.0,
    rush_yds: 240.0,
    rush_td: 3.0,
    pass_yds: 4150,
    pass_td: 30,
    int: 12,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4036378.png",
    notes: "Prolific passer surrounded by a young, talented receiver group.",
  },
  {
    player_id: 4431452,
    name: "Drake Maye",
    position: "QB",
    team: "NE",
    bye: 14,
    age: 24.1,
    ros_pct: 93.0,
    adp: 82.0,
    adp_rank: 34,
    proj_pts: 285.0,
    proj_avg: 16.8,
    rush_att: 75.0,
    rush_yds: 420.0,
    rush_td: 4.0,
    pass_yds: 3800,
    pass_td: 24,
    int: 13,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4431452.png",
    notes: "Rocket-armed dual threat with immense second-year progression upside.",
  },
  {
    player_id: 4432577,
    name: "Brandon Aubrey",
    position: "K",
    team: "DAL",
    bye: 7,
    age: 31.3,
    ros_pct: 99.0,
    adp: 148.0,
    adp_rank: 148,
    proj_pts: 148.0,
    proj_avg: 8.7,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432577.png",
    notes: "60+ yard field goal machine with highest projected kicking total in the league.",
  },
  {
    player_id: 579,
    name: "Jesse Fernandez",
    position: "K",
    team: "DEN",
    bye: 8,
    age: 26.0,
    ros_pct: 98.0,
    adp: 151.0,
    adp_rank: 151,
    proj_pts: 171.0,
    proj_avg: 10.1,
    headshot_url: "/static/players/579.webp",
    notes: "Elite 98 OVR franchise kicker with league-leading accuracy and leg strength.",
  },
  {
    player_id: 311,
    name: "Cam Little",
    position: "K",
    team: "IND",
    bye: 13,
    age: 23.0,
    ros_pct: 97.5,
    adp: 152.0,
    adp_rank: 152,
    proj_pts: 169.2,
    proj_avg: 10.0,
    headshot_url: "/static/players/311.webp",
    notes: "Booming leg with 94 OVR rating providing steady high-volume scoring.",
  },
  {
    player_id: 737,
    name: "Brian Westbrook",
    position: "K",
    team: "BUF",
    bye: 12,
    age: 25.0,
    ros_pct: 97.0,
    adp: 153.0,
    adp_rank: 153,
    proj_pts: 167.4,
    proj_avg: 9.8,
    headshot_url: "/static/players/737.webp",
    notes: "High-scoring offense multiplier in Buffalo with elite clutch rating.",
  },
  {
    player_id: 523,
    name: "Matthew Falk",
    position: "K",
    team: "NYJ",
    bye: 12,
    age: 27.0,
    ros_pct: 96.5,
    adp: 154.0,
    adp_rank: 154,
    proj_pts: 167.4,
    proj_avg: 9.8,
    headshot_url: "/static/players/523.webp",
    notes: "Consistent 90 OVR kicker with automatic touchback distance.",
  },
  {
    player_id: 15683,
    name: "Justin Tucker",
    position: "K",
    team: "BAL",
    bye: 14,
    age: 36.9,
    ros_pct: 97.0,
    adp: 155.0,
    adp_rank: 155,
    proj_pts: 138.0,
    proj_avg: 8.1,
    headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/15683.png",
    notes: "Clutch veteran in one of the highest scoring offenses in the AFC.",
  },
  {
    player_id: 9901,
    name: "San Francisco 49ers",
    position: "DEF",
    team: "SF",
    bye: 8,
    age: 27.0,
    ros_pct: 98.0,
    adp: 98.0,
    adp_rank: 37,
    proj_pts: 132.0,
    proj_avg: 7.8,
    headshot_url: "/static/players/robot_avatar.webp",
    notes: "Nick Bosa-led front four creating constant strip-sacks and turnovers.",
  },
  {
    player_id: 9902,
    name: "Baltimore Ravens",
    position: "DEF",
    team: "BAL",
    bye: 14,
    age: 26.5,
    ros_pct: 97.5,
    adp: 104.0,
    adp_rank: 38,
    proj_pts: 129.0,
    proj_avg: 7.6,
    headshot_url: "/static/players/robot_avatar.webp",
    notes: "Smash-mouth defense specializing in pressure rate and red-zone stops.",
  },
];

interface NapperDraftRoomProps {
  leagueId?: number;
  initialSlot?: number;
  onBack: () => void;
  onLeagueCreated?: (newLeagueId: number) => void;
  onExitToMu?: () => void;
}

const MOCK_ARCHETYPE_TEAMS: Record<string, Array<{ name: string; handle: string; persona: string }>> = {
  ZERO_RB_ZEALOT: [
    { name: "Zero-RB Theory", handle: "@ZeroHero", persona: "ZERO_RB_ZEALOT" },
    { name: "PPR Machines", handle: "@PPRGod", persona: "ZERO_RB_ZEALOT" },
    { name: "Air Raid Society", handle: "@AirRaidHQ", persona: "ZERO_RB_ZEALOT" },
    { name: "Late Round RB Lab", handle: "@LateBacks", persona: "ZERO_RB_ZEALOT" },
    { name: "Target Monster", handle: "@TargetShare", persona: "ZERO_RB_ZEALOT" },
  ],
  HERO_RB: [
    { name: "Hero RB Squad", handle: "@AnchorBack", persona: "HERO_RB" },
    { name: "Robust RB Lab", handle: "@BellCowBros", persona: "HERO_RB" },
    { name: "Workhorse Dynasty", handle: "@Workhorse", persona: "HERO_RB" },
    { name: "Ground & Pound", handle: "@HeavyTrench", persona: "HERO_RB" },
    { name: "Bellcow Believers", handle: "@BellcowBros", persona: "HERO_RB" },
  ],
  ANALYTICS_NERD: [
    { name: "Data Analytics", handle: "@DataDog", persona: "ANALYTICS_NERD" },
    { name: "VORP Machine", handle: "@QuantKing", persona: "ANALYTICS_NERD" },
    { name: "Draft Board Wizards", handle: "@ScoutMaster", persona: "ANALYTICS_NERD" },
    { name: "Regression Models", handle: "@StatSigma", persona: "ANALYTICS_NERD" },
    { name: "Expected Points HQ", handle: "@xFP_Metrics", persona: "ANALYTICS_NERD" },
  ],
  TRASH_TALKER: [
    { name: "Gridiron Dominance", handle: "@Champ2035", persona: "TRASH_TALKER" },
    { name: "Smashmouth Crew", handle: "@HeavyHitter", persona: "TRASH_TALKER" },
    { name: "Trophy Chasers", handle: "@RingCollector", persona: "TRASH_TALKER" },
    { name: "No Fear Fantasy", handle: "@SwaggerKing", persona: "TRASH_TALKER" },
    { name: "Scoreboard Watchers", handle: "@CheckTheBoard", persona: "TRASH_TALKER" },
  ],
  WAIVER_HAWK: [
    { name: "Waiver Wire Kings", handle: "@WireHawk", persona: "WAIVER_HAWK" },
    { name: "FAAB Bidding Lab", handle: "@FAABMaxer", persona: "WAIVER_HAWK" },
    { name: "Depth Hoarders", handle: "@BenchDepth", persona: "WAIVER_HAWK" },
    { name: "Midnight Churners", handle: "@WaiverNinja", persona: "WAIVER_HAWK" },
    { name: "Upside Lottery Co", handle: "@HandcuffLotto", persona: "WAIVER_HAWK" },
  ],
  DIE_HARD: [
    { name: "Franchise DieHards", handle: "@DieHardFan", persona: "DIE_HARD" },
    { name: "Sunday Loyalty", handle: "@BleedColors", persona: "DIE_HARD" },
    { name: "Legacy Believers", handle: "@OldSchoolGrit", persona: "DIE_HARD" },
    { name: "Eye Test Only", handle: "@FilmRoomEye", persona: "DIE_HARD" },
    { name: "Hometown Heroes", handle: "@CivicPride", persona: "DIE_HARD" },
  ],
  TRADE_FIEND: [
    { name: "Trade Block Titans", handle: "@BlockBuster", persona: "TRADE_FIEND" },
    { name: "Buy Low Syndicate", handle: "@MarketFlip", persona: "TRADE_FIEND" },
    { name: "Asset Allocators", handle: "@ConsolidationKing", persona: "TRADE_FIEND" },
    { name: "Two-For-One Lab", handle: "@PackageDeal", persona: "TRADE_FIEND" },
    { name: "Touchdown Todd", handle: "@TouchdownTodd", persona: "TRADE_FIEND" },
  ],
  UNDERDOG: [
    { name: "Sleeper Syndicate", handle: "@SleeperSteve", persona: "UNDERDOG" },
    { name: "Breakout Blueprint", handle: "@NextGenStars", persona: "UNDERDOG" },
    { name: "Young Gun Dynasty", handle: "@YouthMovement", persona: "UNDERDOG" },
    { name: "Ceiling Chasers", handle: "@CeilingChasers", persona: "UNDERDOG" },
    { name: "Cinderella Squad", handle: "@UnderdogHQ", persona: "UNDERDOG" },
  ],
  RB_HEAVY: [
    { name: "Heavy Trench", handle: "@HeavyTrench", persona: "RB_HEAVY" },
    { name: "Ground & Pound", handle: "@GroundPound", persona: "RB_HEAVY" },
    { name: "Smashmouth Syndicate", handle: "@SmashTrench", persona: "RB_HEAVY" },
    { name: "Bellcow Dynasty", handle: "@BellcowKings", persona: "RB_HEAVY" },
    { name: "Trench Bullies", handle: "@TrenchBullies", persona: "RB_HEAVY" },
  ],
  EARLY_QB: [
    { name: "Gunslinger Dynasty", handle: "@GunslingerHQ", persona: "EARLY_QB" },
    { name: "Field Generals", handle: "@FieldGeneral", persona: "EARLY_QB" },
    { name: "Elite Arms Lab", handle: "@EliteArms", persona: "EARLY_QB" },
    { name: "Pocket Kings", handle: "@PocketKings", persona: "EARLY_QB" },
    { name: "Dual-Threat Society", handle: "@DualThreat", persona: "EARLY_QB" },
  ],
  EARLY_TE: [
    { name: "Middle Seam", handle: "@MiddleSeam", persona: "EARLY_TE" },
    { name: "Unicorn Tight Ends", handle: "@UnicornTE", persona: "EARLY_TE" },
    { name: "Endzone Enforcers", handle: "@TE_Enforcers", persona: "EARLY_TE" },
    { name: "Y-Option Lab", handle: "@YOption", persona: "EARLY_TE" },
    { name: "Seam Stretchers", handle: "@SeamStretcher", persona: "EARLY_TE" },
  ],
  BPA: [
    { name: "Pure Consensus", handle: "@ConsensusBoard", persona: "BPA" },
    { name: "Best Board Available", handle: "@TrueBPA", persona: "BPA" },
    { name: "Value Over Replacement", handle: "@VORP_Pure", persona: "BPA" },
    { name: "Board Discipline", handle: "@BoardDiscipline", persona: "BPA" },
    { name: "Market Consensus", handle: "@MarketConsensus", persona: "BPA" },
  ],
};

const DEFAULT_AI_TEAMS = [
  { name: "Zero-RB Theory", handle: "@ZeroHero", persona: "ZERO_RB_ZEALOT" },
  { name: "Data Analytics", handle: "@DataDog", persona: "ANALYTICS_NERD" },
  { name: "Trade Block Titans", handle: "@BlockBuster", persona: "TRADE_FIEND" },
  { name: "Sleeper Syndicate", handle: "@SleeperSteve", persona: "UNDERDOG" },
  { name: "Gridiron Dominance", handle: "@Champ2035", persona: "TRASH_TALKER" },
  { name: "Hero RB Squad", handle: "@AnchorBack", persona: "HERO_RB" },
  { name: "Waiver Wire Kings", handle: "@WireHawk", persona: "WAIVER_HAWK" },
  { name: "Franchise DieHards", handle: "@DieHardFan", persona: "DIE_HARD" },
];

function buildMockTeamsAndPicks(
  teamsCount: number,
  roundsCount: number,
  userSlot: number,
  composition?: Record<string, number>,
  randomizeAiOrder: boolean = true
): { teams: NapperDraftTeam[]; picks: NapperDraftPick[] } {
  const aiSlots = Math.max(1, teamsCount - 1);
  const selectedPersonas: string[] = [];

  if (composition && Object.values(composition).reduce((a, b) => a + b, 0) > 0) {
    for (const [persona, count] of Object.entries(composition)) {
      if (persona in MOCK_ARCHETYPE_TEAMS) {
        for (let i = 0; i < count; i++) {
          selectedPersonas.push(persona);
        }
      }
    }
  }

  const allKeys = Object.keys(MOCK_ARCHETYPE_TEAMS);
  let pIdx = 0;
  while (selectedPersonas.length < aiSlots) {
    selectedPersonas.push(allKeys[pIdx % allKeys.length]);
    pIdx++;
  }
  selectedPersonas.length = aiSlots;

  // Build AI manager templates
  const usedIndices: Record<string, number> = {};
  const aiManagers = selectedPersonas.map((persona) => {
    const pool = MOCK_ARCHETYPE_TEAMS[persona] || MOCK_ARCHETYPE_TEAMS.ANALYTICS_NERD;
    const idx = (usedIndices[persona] || 0) % pool.length;
    usedIndices[persona] = (usedIndices[persona] || 0) + 1;
    return { ...pool[idx] };
  });

  if (randomizeAiOrder) {
    // Fisher-Yates shuffle so no two drafts have the same order
    for (let i = aiManagers.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [aiManagers[i], aiManagers[j]] = [aiManagers[j], aiManagers[i]];
    }
  }

  let aiCursor = 0;
  const teams: NapperDraftTeam[] = Array.from({ length: teamsCount }, (_, i) => {
    const slot = i + 1;
    const isUser = slot === userSlot;
    if (isUser) {
      return {
        id: slot,
        slot,
        name: "The Straw Hat Pirates",
        is_user: true,
        is_ai: false,
        avatar_url: "/static/players/robot_avatar.webp",
        manager_handle: "@jrereyi2000",
        persona: "USER",
        roster: [],
      };
    }
    const aiManager = aiManagers[aiCursor++] || {
      name: `Team ${slot}`,
      handle: `@Manager${slot}`,
      persona: "ANALYTICS_NERD",
    };
    return {
      id: slot,
      slot,
      name: aiManager.name,
      is_user: false,
      is_ai: true,
      avatar_url: "/static/players/robot_avatar.webp",
      manager_handle: aiManager.handle,
      persona: aiManager.persona,
      roster: [],
    };
  });

  const picks: NapperDraftPick[] = [];
  for (let r = 1; r <= roundsCount; r++) {
    const isOdd = r % 2 === 1;
    for (let p = 1; p <= teamsCount; p++) {
      const overall = (r - 1) * teamsCount + p;
      const slot = isOdd ? p : teamsCount - p + 1;
      const team = teams.find((t) => t.slot === slot);
      picks.push({
        pick_overall: overall,
        round: r,
        round_pick: p,
        team_id: team?.id || slot,
        team_name: team?.name || `Team ${slot}`,
        team_slot: slot,
        player: null,
      });
    }
  }

  return { teams, picks };
}

export const NapperDraftRoom: React.FC<NapperDraftRoomProps> = ({
  leagueId = 1,
  initialSlot,
  onBack,
  onLeagueCreated,
}) => {
  const defaultTeamsCount = 10;
  const defaultRoundsCount = 15;

  const [archetypeComposition, setArchetypeComposition] = useState<Record<string, number>>(() =>
    ARCHETYPE_PRESETS[0].getCounts(defaultTeamsCount - 1)
  );
  const [archetypePresetName, setArchetypePresetName] = useState<string>("Balanced Mix");
  const [archetypeModalOpen, setArchetypeModalOpen] = useState(false);

  const [draftState, setDraftState] = useState<NapperDraftState>(() => {
    const teamsCount = defaultTeamsCount;
    const roundsCount = defaultRoundsCount;
    // Default to randomized slot if not explicitly provided
    const userSlot = initialSlot && initialSlot >= 1 && initialSlot <= teamsCount
      ? initialSlot
      : Math.floor(Math.random() * teamsCount) + 1;
    const initialComp = ARCHETYPE_PRESETS[0].getCounts(teamsCount - 1);
    const { teams: initialTeams, picks: initialPicks } = buildMockTeamsAndPicks(
      teamsCount,
      roundsCount,
      userSlot,
      initialComp,
      true
    );

    return {
      id: `mock-${Date.now()}`,
      league_id: leagueId,
      title: `${teamsCount}-Team PPR Snake Mock`,
      teams_count: teamsCount,
      rounds_count: roundsCount,
      seconds_per_pick: 120,
      status: "NOT_STARTED",
      current_pick_index: 0,
      user_slot: userSlot,
      auto_pick: false,
      teams: initialTeams,
      picks: initialPicks,
      roster_slots: ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN"],
    };
  });

  const [availablePlayers, setAvailablePlayers] = useState<NapperDraftPlayer[]>(MOCK_DRAFT_PLAYERS);
  const [queuedIds, setQueuedIds] = useState<number[]>([]);
  const [comments, setComments] = useState<NapperDraftComment[]>([
    {
      id: "c-1",
      sender_name: "Draft Commissioner",
      sender_handle: "@DraftBot",
      message: "⚡ Welcome to the Napper Draft Room! Claim your draft slot and press START DRAFT when ready.",
      timestamp: "Just now",
      is_bot: true,
    },
  ]);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [presentationMode, setPresentationMode] = useState(false);
  const [simSpeedMs, setSimSpeedMs] = useState(600);
  const [timerSeconds, setTimerSeconds] = useState(120);
  const [isSimulatingAll, setIsSimulatingAll] = useState(false);
  const [selectedNotesPlayer, setSelectedNotesPlayer] = useState<NapperDraftPlayer | null>(null);
  const [assistantModalOpen, setAssistantModalOpen] = useState(false);
  const [recapModalOpen, setRecapModalOpen] = useState(false);
  const [orderModalOpen, setOrderModalOpen] = useState(false);
  const [isSelectionBoxOpen, setIsSelectionBoxOpen] = useState<boolean>(true);

  // Fetch live draft state from backend if available
  useEffect(() => {
    if (!leagueId) return;
    fetch(`${API_BASE}/api/napper/league/${leagueId}/draft`)
      .then((res) => res.json())
      .then((data) => {
        if (data && data.draft_id) {
          const serverTeams: NapperDraftTeam[] = (data.teams_info || []).map((t: any) => ({
            id: t.slot_num,
            slot: t.slot_num,
            name: t.team_name,
            is_user: t.is_user,
            is_ai: !t.is_user,
            avatar_url: t.avatar_url || "/static/players/robot_avatar.webp",
            manager_handle: t.manager_handle || `@Team${t.slot_num}`,
            roster: data.rosters?.[String(t.slot_num)] || [],
          }));

          const serverPicks: NapperDraftPick[] = (data.picks || []).map((p: any) => ({
            pick_overall: p.overall_pick,
            round: p.round_num,
            round_pick: p.pick_num,
            team_id: p.team_id || p.slot_num,
            team_name: p.team_name,
            team_slot: p.slot_num,
            player: p.player
              ? {
                  player_id: p.player.id,
                  name: p.player.name,
                  position: p.player.position,
                  team: p.player.team,
                  bye: 7,
                  age: 25,
                  ros_pct: 95,
                  adp: p.player.adp,
                  adp_rank: p.player.adp,
                  proj_pts: p.player.projected_points,
                  proj_avg: p.player.projected_points / 17,
                  headshot_url: p.player.headshot_url,
                  is_drafted: true,
                }
              : null,
          }));

          setDraftState((prev) => ({
            ...prev,
            id: data.draft_id,
            status: data.status === "PENDING" ? "NOT_STARTED" : data.status,
            current_pick_index: Math.max(0, (data.current_pick || 1) - 1),
            user_slot: data.user_slot || prev.user_slot,
            teams: serverTeams.length > 0 ? serverTeams : prev.teams,
            picks: serverPicks.length > 0 ? serverPicks : prev.picks,
          }));
        }
      })
      .catch(() => {
        // Graceful fallback to offline client-side simulation
      });
  }, [leagueId]);

  // Fetch real players for season 2035 from backend
  useEffect(() => {
    let isMounted = true;
    const fetchPlayerPool = async () => {
      try {
        const draftIdNum = Number(draftState.id);
        const validDraftId = !isNaN(draftIdNum) && draftIdNum > 0 ? draftIdNum : 0;
        let res = await fetch(`${API_BASE}/api/napper/draft/${validDraftId}/available_players?limit=600`);
        if (!res.ok) {
          res = await fetch(`${API_BASE}/api/fantasy/projections?season=2035&week=1&limit=600`);
        }
        if (res.ok) {
          const data = await res.json();
          const rawPlayers = data.players || [];
          if (rawPlayers.length > 0 && isMounted) {
            const mapped: NapperDraftPlayer[] = rawPlayers.map((p: any) => ({
              player_id: p.id || p.player_id,
              name: p.name || p.player_name,
              position: p.position === "HB" || p.position === "FB" ? "RB" : p.position,
              team: p.team || p.team_abbr || "NFL",
              bye: p.bye ?? (p.team_id ? (p.team_id % 10) + 5 : 7),
              age: p.age ?? 25,
              ros_pct: p.ros_pct ?? 95,
              adp: p.adp ?? 999,
              adp_rank: p.adp ?? 999,
              proj_pts: p.projections?.pts ?? p.proj_pts ?? 12.0,
              proj_avg: p.projections?.avg ?? p.avg_pts ?? 11.5,
              rush_att: p.rush?.rush ?? p.rush_att ?? 0,
              rush_yds: p.rush?.yds ?? p.rush_yds ?? 0,
              rush_td: p.rush?.td ?? p.rush_td ?? 0,
              rec: p.rec?.rec ?? p.rec ?? 0,
              rec_yds: p.rec?.yds ?? p.rec_yds ?? 0,
              rec_td: p.rec?.td ?? p.rec_td ?? 0,
              pass_yds: p.pass_yds ?? 0,
              pass_td: p.pass_td ?? 0,
              int: p.int ?? 0,
              headshot_url: p.headshot_url || `/static/players/${p.id || p.player_id}.webp`,
              is_drafted: false,
            }));
            setAvailablePlayers(mapped);
          }
        }
      } catch {
        // Fallback to MOCK_DRAFT_PLAYERS
      }
    };
    fetchPlayerPool();
    return () => {
      isMounted = false;
    };
  }, [draftState.id]);

  // Web Audio Chime Generator
  const playChime = useCallback((type: "USER_TURN" | "PICK_MADE" | "DRAFT_DONE") => {
    if (!soundEnabled) return;
    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (type === "USER_TURN") {
        // High double-beep
        osc.type = "sine";
        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc.frequency.setValueAtTime(880.0, ctx.currentTime + 0.12); // A5
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
        osc.start();
        osc.stop(ctx.currentTime + 0.45);
      } else if (type === "PICK_MADE") {
        // Subtle soft drop chime
        osc.type = "triangle";
        osc.frequency.setValueAtTime(440.0, ctx.currentTime); // A4
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.005, ctx.currentTime + 0.2);
        osc.start();
        osc.stop(ctx.currentTime + 0.25);
      } else if (type === "DRAFT_DONE") {
        // Victory fanfare
        osc.type = "sine";
        osc.frequency.setValueAtTime(523.25, ctx.currentTime); // C5
        osc.frequency.setValueAtTime(659.25, ctx.currentTime + 0.15); // E5
        osc.frequency.setValueAtTime(783.99, ctx.currentTime + 0.3); // G5
        gain.gain.setValueAtTime(0.2, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.7);
        osc.start();
        osc.stop(ctx.currentTime + 0.75);
      }
    } catch {
      // AudioContext not allowed before gesture or not supported
    }
  }, [soundEnabled]);

  // Current On-The-Clock Calculations
  const currentPick = draftState.picks[draftState.current_pick_index];
  const isUserTurn =
    draftState.status === "IN_PROGRESS" &&
    Boolean(currentPick && currentPick.team_slot === draftState.user_slot);
  const userTeam = draftState.teams.find((t) => t.slot === draftState.user_slot);
  const userRoster = userTeam?.roster || [];

  // Play chime when user turn begins
  const prevIsUserTurnRef = useRef(false);
  useEffect(() => {
    if (isUserTurn && !prevIsUserTurnRef.current) {
      playChime("USER_TURN");
    }
    prevIsUserTurnRef.current = isUserTurn;
  }, [isUserTurn, playChime]);

  // Claim Slot Action: Updates user's slot and swaps teams
  const handleClaimSlot = (slot: number) => {
    if (draftState.status !== "NOT_STARTED") return;
    if (slot < 1 || slot > draftState.teams_count) return;

    const oldSlot = draftState.user_slot;
    if (oldSlot === slot) return;

    setDraftState((prev) => {
      const targetTeam = prev.teams.find((t) => t.slot === slot);
      const userTeam = prev.teams.find((t) => t.slot === oldSlot);

      const updatedTeams = prev.teams.map((t) => {
        if (t.slot === slot) {
          return {
            ...t,
            name: userTeam?.name || "The Straw Hat Pirates",
            is_user: true,
            is_ai: false,
            manager_handle: userTeam?.manager_handle || "@jrereyi2000",
            persona: "USER",
          };
        }
        if (t.slot === oldSlot) {
          return {
            ...t,
            name: targetTeam?.name || `Team ${oldSlot}`,
            is_user: false,
            is_ai: true,
            manager_handle: targetTeam?.manager_handle || `@Manager${oldSlot}`,
            persona: targetTeam?.persona || "ANALYTICS_NERD",
          };
        }
        return t;
      });

      const newUserTeam = updatedTeams.find((t) => t.slot === slot);
      const newOldTeam = updatedTeams.find((t) => t.slot === oldSlot);

      const updatedPicks = prev.picks.map((p) => {
        if (p.team_slot === slot) {
          return {
            ...p,
            team_name: newUserTeam?.name || "The Straw Hat Pirates",
            team_id: newUserTeam?.id || slot,
          };
        }
        if (p.team_slot === oldSlot) {
          return {
            ...p,
            team_name: newOldTeam?.name || `Team ${oldSlot}`,
            team_id: newOldTeam?.id || oldSlot,
          };
        }
        return p;
      });

      return {
        ...prev,
        user_slot: slot,
        teams: updatedTeams,
        picks: updatedPicks,
      };
    });

    const draftIdNum = Number(draftState.id);
    if (!isNaN(draftIdNum) && draftIdNum > 0) {
      fetch(`${API_BASE}/api/napper/draft/${draftIdNum}/claim_slot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slot }),
      }).catch(() => {});
    }
  };

  // Randomize Order Action: Re-rolls draft spot and shuffles all opponents
  const handleRandomizeOrder = (keepUser: boolean = false) => {
    if (draftState.status !== "NOT_STARTED") return;
    const count = draftState.teams_count;
    let newSlot = draftState.user_slot;
    if (!keepUser) {
      const candidates = Array.from({ length: count }, (_, i) => i + 1).filter(
        (s) => s !== draftState.user_slot
      );
      newSlot = candidates[Math.floor(Math.random() * candidates.length)] || 1;
    }

    const { teams: newTeams, picks: newPicks } = buildMockTeamsAndPicks(
      count,
      draftState.rounds_count,
      newSlot,
      archetypeComposition,
      true
    );

    setDraftState((prev) => ({
      ...prev,
      user_slot: newSlot,
      teams: newTeams,
      picks: newPicks,
      current_pick_index: 0,
    }));
    setAvailablePlayers((prev) => prev.map((p) => ({ ...p, is_drafted: false })));

    const draftIdNum = Number(draftState.id);
    if (!isNaN(draftIdNum) && draftIdNum > 0) {
      fetch(`${API_BASE}/api/napper/draft/${draftIdNum}/randomize_order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_user_slot: keepUser }),
      }).catch(() => {});
    }
  };

  // Apply customized archetype composition
  const handleApplyArchetypes = (newComposition: Record<string, number>, presetName?: string) => {
    if (draftState.status !== "NOT_STARTED") return;
    setArchetypeComposition(newComposition);
    if (presetName) setArchetypePresetName(presetName);

    const { teams: newTeams, picks: newPicks } = buildMockTeamsAndPicks(
      draftState.teams_count,
      draftState.rounds_count,
      draftState.user_slot,
      newComposition,
      true
    );

    setDraftState((prev) => ({
      ...prev,
      teams: newTeams,
      picks: newPicks,
      current_pick_index: 0,
    }));

    const draftIdNum = Number(draftState.id);
    if (!isNaN(draftIdNum) && draftIdNum > 0) {
      fetch(`${API_BASE}/api/napper/draft/${draftIdNum}/set_archetypes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archetype_composition: newComposition }),
      }).catch(() => {});
    }
  };

  // Apply manual draft order customization
  const handleApplyDraftOrder = (orderedTeams: NapperDraftTeam[]) => {
    if (draftState.status !== "NOT_STARTED") return;

    // Find new user slot
    const userTeam = orderedTeams.find((t) => t.is_user);
    const newUserSlot = userTeam ? userTeam.slot : draftState.user_slot;

    // Map slot to team
    const teamBySlot: Record<number, NapperDraftTeam> = {};
    orderedTeams.forEach((t) => {
      teamBySlot[t.slot] = t;
    });

    // Re-assign picks according to new team slots
    const updatedPicks = draftState.picks.map((p) => {
      const assignedTeam = teamBySlot[p.team_slot];
      return {
        ...p,
        team_id: assignedTeam?.id || p.team_slot,
        team_name: assignedTeam?.name || `Team ${p.team_slot}`,
      };
    });

    setDraftState((prev) => ({
      ...prev,
      user_slot: newUserSlot,
      teams: orderedTeams,
      picks: updatedPicks,
      current_pick_index: 0,
    }));

    setComments((prev) => [
      ...prev,
      {
        id: `c-order-${Date.now()}`,
        sender_name: "Draft Commissioner",
        sender_handle: "@DraftBot",
        message: `📋 Draft order customized! You are drafting from Slot #${newUserSlot}.`,
        timestamp: "Just now",
        is_bot: true,
      },
    ]);

    const draftIdNum = Number(draftState.id);
    if (!isNaN(draftIdNum) && draftIdNum > 0) {
      fetch(`${API_BASE}/api/napper/draft/${draftIdNum}/set_order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slot_order: orderedTeams.map((t) => t.slot),
          teams: orderedTeams.map((t) => ({
            slot_num: t.slot,
            team_id: t.id,
            team_name: t.name,
            manager_handle: t.manager_handle,
            persona: t.persona,
            avatar_url: t.avatar_url,
            is_user: !!t.is_user,
          })),
        }),
      }).catch(() => {});
    }
  };

  // Switch Team Count (8, 10, 12 Teams) and regenerate snake picks
  const handleTeamCountChange = (newCount: number) => {
    if (draftState.status !== "NOT_STARTED") return;
    const newComp = ARCHETYPE_PRESETS[0].getCounts(newCount - 1);
    setArchetypeComposition(newComp);
    setArchetypePresetName("Balanced Mix");
    const newSlot = Math.floor(Math.random() * newCount) + 1;
    const { teams: newTeams, picks: newPicks } = buildMockTeamsAndPicks(
      newCount,
      draftState.rounds_count,
      newSlot,
      newComp,
      true
    );
    setDraftState((prev) => ({
      ...prev,
      teams_count: newCount,
      user_slot: newSlot,
      title: `${newCount}-Team PPR Snake Mock`,
      teams: newTeams,
      picks: newPicks,
      current_pick_index: 0,
    }));
    setAvailablePlayers((prev) => prev.map((p) => ({ ...p, is_drafted: false })));
    setQueuedIds([]);

    fetch(`${API_BASE}/api/napper/drafts/create_mock`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        teams_count: newCount,
        scoring_type: "PPR",
        user_slot: newSlot,
        rounds: draftState.rounds_count,
        is_superflex: true,
        archetype_composition: newComp,
      }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data && data.id) {
          setDraftState((prev) => ({ ...prev, id: String(data.id) }));
        }
      })
      .catch(() => {});
  };

  // Customize Rounds Count (12, 14, 15, 16)
  const handleRoundsChange = (newRounds: number) => {
    if (draftState.status !== "NOT_STARTED") return;
    const { teams: newTeams, picks: newPicks } = buildMockTeamsAndPicks(
      draftState.teams_count,
      newRounds,
      draftState.user_slot,
      archetypeComposition,
      false
    );
    setDraftState((prev) => ({
      ...prev,
      rounds_count: newRounds,
      teams: newTeams,
      picks: newPicks,
      current_pick_index: 0,
    }));
  };

  // Submit a pick (by user or AI)
  const executePick = useCallback((playerToPick: NapperDraftPlayer) => {
    setDraftState((prev) => {
      if (prev.current_pick_index >= prev.picks.length) return prev;

      const pickIndex = prev.current_pick_index;
      const targetPick = prev.picks[pickIndex];
      const pickingTeamSlot = targetPick.team_slot;

      // Mark player as drafted
      const draftedPlayer: NapperDraftPlayer = {
        ...playerToPick,
        is_drafted: true,
        drafted_by_team_id: targetPick.team_id,
        draft_pick_num: targetPick.pick_overall,
      };

      // Update pick in picks array
      const updatedPicks = prev.picks.map((p, idx) => {
        if (idx === pickIndex) {
          return {
            ...p,
            player: draftedPlayer,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          };
        }
        return p;
      });

      // Update team's roster
      const updatedTeams = prev.teams.map((t) => {
        if (t.slot === pickingTeamSlot) {
          return {
            ...t,
            roster: [...t.roster, draftedPlayer],
          };
        }
        return t;
      });

      const nextPickIndex = pickIndex + 1;
      const isComplete = nextPickIndex >= prev.picks.length;

      // Add archetype-tailored commentary
      const pickingTeam = prev.teams.find((t) => t.slot === pickingTeamSlot);
      const persona = pickingTeam?.persona || "AI";
      let commentText = `${targetPick.team_name} selects ${draftedPlayer.position} ${draftedPlayer.name} with pick ${targetPick.round}.${targetPick.round_pick} (Overall #${targetPick.pick_overall})`;

      const diff = targetPick.pick_overall - draftedPlayer.adp;
      const isSteal = diff >= 6;
      const isReach = diff <= -6;

      if (pickingTeamSlot === prev.user_slot) {
        if (isSteal) {
          commentText = `🔥 Great value! You drafted ${draftedPlayer.name} (ADP #${draftedPlayer.adp_rank}) at pick #${targetPick.pick_overall}.`;
        } else if (isReach) {
          commentText = `👀 Aggressive reach! You took ${draftedPlayer.name} ahead of consensus ADP at pick #${targetPick.pick_overall}.`;
        } else {
          commentText = `Solid selection with ${draftedPlayer.position} ${draftedPlayer.name} at pick #${targetPick.pick_overall}.`;
        }
      } else if (persona === "ZERO_RB_ZEALOT") {
        if (draftedPlayer.position === "RB") {
          commentText = `Breaking my Zero-RB rule for ${draftedPlayer.name} at #${targetPick.pick_overall}. The value was simply unavoidable.`;
        } else {
          commentText = `Zero-RB masterclass in progress. Locking down ${draftedPlayer.position} ${draftedPlayer.name} with projected ${draftedPlayer.proj_pts.toFixed(0)} FPTS.`;
        }
      } else if (persona === "HERO_RB") {
        if (draftedPlayer.position === "RB") {
          commentText = `Anchor back secured! ${draftedPlayer.name} gives me the bellcow foundation I needed at pick #${targetPick.pick_overall}.`;
        } else {
          commentText = `Bolstering pass-catchers around my anchor back with ${draftedPlayer.name} at #${targetPick.pick_overall}.`;
        }
      } else if (persona === "ANALYTICS_NERD") {
        if (isSteal) {
          commentText = `My VORP algorithms flagged ${draftedPlayer.name} as a +${diff} pick market inefficiency. Automatic trigger.`;
        } else {
          commentText = `Optimal expected-value pick: ${draftedPlayer.name} projects for ${draftedPlayer.proj_pts.toFixed(0)} FPTS with an elite target/touch profile.`;
        }
      } else if (persona === "TRASH_TALKER") {
        if (isReach) {
          commentText = `Rankings are for cowards. ${draftedPlayer.name} is going to dominate the entire league this year. Good luck stopping us.`;
        } else {
          commentText = `Thanks for letting ${draftedPlayer.name} fall to me. You all just handed me the championship.`;
        }
      } else if (persona === "WAIVER_HAWK") {
        commentText = `Target volume and versatile snaps secured. ${draftedPlayer.name} fits my roster depth perfectly at pick #${targetPick.pick_overall}.`;
      } else if (persona === "DIE_HARD") {
        commentText = `Pure grit and veteran leadership. ${draftedPlayer.name} brings championship DNA to this franchise at pick #${targetPick.pick_overall}.`;
      } else if (persona === "TRADE_FIEND") {
        commentText = `Taking ${draftedPlayer.name} at #${targetPick.pick_overall}. Tremendous value, and my DMs are wide open if anyone wants to talk trade right now.`;
      } else if (persona === "UNDERDOG") {
        commentText = `The room is sleeping on ${draftedPlayer.name} at pick #${targetPick.pick_overall}. Massive breakout ceiling incoming!`;
      }

      const botPickComment: NapperDraftComment = {
        id: `pick-${pickIndex}`,
        sender_name: pickingTeam?.name || "DraftBot",
        sender_handle: pickingTeam?.manager_handle || "@DraftBot",
        message: commentText,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        is_bot: true,
        pick_badge: {
          pick_num: `${targetPick.round}.${targetPick.round_pick}`,
          player_name: draftedPlayer.name,
          pos: draftedPlayer.position,
        },
      };

      setComments((c) => [botPickComment, ...c]);

      // Remove from queue if it was queued
      setQueuedIds((q) => q.filter((id) => id !== draftedPlayer.player_id));

      if (isComplete) {
        setTimeout(() => {
          playChime("DRAFT_DONE");
          setRecapModalOpen(true);
        }, 600);
      } else {
        playChime("PICK_MADE");
      }

      return {
        ...prev,
        current_pick_index: nextPickIndex,
        status: isComplete ? "COMPLETED" : prev.status,
        picks: updatedPicks,
        teams: updatedTeams,
      };
    });

    // Update available players state
    setAvailablePlayers((prev) =>
      prev.map((p) => (p.player_id === playerToPick.player_id ? { ...p, is_drafted: true } : p))
    );
    setTimerSeconds(draftState.seconds_per_pick);
  }, [draftState.picks.length, draftState.seconds_per_pick, playChime]);

  // AI Logic to select the best player for an AI team based on its Archetype and team needs
  const selectAiPlayer = useCallback(
    (slotNum?: number): NapperDraftPlayer | null => {
      const unpicked = availablePlayers.filter((p) => !p.is_drafted);
      if (unpicked.length === 0) return null;

      const currentP = draftState.picks[draftState.current_pick_index];
      const targetSlot = slotNum ?? currentP?.team_slot;
      const team = draftState.teams.find((t) => t.slot === targetSlot);
      const persona = team?.persona || "ANALYTICS_NERD";
      const currentRoster = team?.roster || [];
      const currentRound = currentP?.round || 1;
      const currentOverall = currentP?.pick_overall || 1;

      // Count positions drafted
      const qbCount = currentRoster.filter((p) => p.position === "QB").length;
      const rbCount = currentRoster.filter((p) => p.position === "RB" || p.position === "HB" || p.position === "FB").length;
      const wrCount = currentRoster.filter((p) => p.position === "WR").length;
      const teCount = currentRoster.filter((p) => p.position === "TE").length;
      const kCount = currentRoster.filter((p) => p.position === "K").length;
      const defCount = currentRoster.filter((p) => p.position === "DEF").length;

      // Starter requirements aligned with fantasy_league_sim LINEUP
      const starterTargets = {
        QB: draftState.rounds_count >= 1 ? 1 : 0,
        RB: draftState.rounds_count >= 8 ? 2 : (draftState.rounds_count >= 2 ? 1 : 0),
        WR: draftState.rounds_count >= 8 ? 2 : (draftState.rounds_count >= 3 ? 1 : 0),
        TE: draftState.rounds_count >= 4 ? 1 : 0,
        K: draftState.rounds_count >= 14 ? 1 : 0,
        DEF: draftState.rounds_count >= 14 ? 1 : 0,
      };

      const needQb = Math.max(0, starterTargets.QB - qbCount);
      const needRb = Math.max(0, starterTargets.RB - rbCount);
      const needWr = Math.max(0, starterTargets.WR - wrCount);
      const needTe = Math.max(0, starterTargets.TE - teCount);
      const needK = Math.max(0, starterTargets.K - kCount);
      const needDef = Math.max(0, starterTargets.DEF - defCount);
      const totalNeed = needQb + needRb + needWr + needTe + needK + needDef;
      const totalRostered = qbCount + rbCount + wrCount + teCount + kCount + defCount;
      const picksLeft = Math.max(1, draftState.rounds_count - totalRostered);
      const forced = totalNeed >= picksLeft;

      // Filter and evaluate top 100 candidates
      const candidates = unpicked.slice(0, 100);
      let bestPlayer: NapperDraftPlayer = candidates[0];
      let bestScore = -999999;

      for (const player of candidates) {
        const rawPos = player.position;
        const pos = (rawPos === "HB" || rawPos === "FB") ? "RB" : rawPos;

        // Hard roster caps (aligned with fantasy_league_sim DRAFT_CAP: max 2 QBs, max 2 TEs)
        if (pos === "QB" && qbCount >= 2) continue;
        if (pos === "RB" && rbCount >= 7) continue;
        if (pos === "WR" && wrCount >= 7) continue;
        if (pos === "TE" && teCount >= 2) continue;
        if (pos === "K" && (kCount >= 1 || currentRound < Math.max(1, draftState.rounds_count - 2))) continue;
        if (pos === "DEF" && (defCount >= 1 || currentRound < Math.max(1, draftState.rounds_count - 2))) continue;

        // Forced starting lineup filling: if remaining picks equal empty starter needs, only draft needed positions
        if (forced) {
          if (pos === "QB" && needQb === 0) continue;
          if (pos === "RB" && needRb === 0) continue;
          if (pos === "WR" && needWr === 0) continue;
          if (pos === "TE" && needTe === 0) continue;
          if (pos === "K" && needK === 0) continue;
          if (pos === "DEF" && needDef === 0) continue;
        }

        // Positional Weighting (aligned with fantasy_league_sim POS_WEIGHT: QB 0.4, TE 0.8, RB/WR 1.0)
        const posWeight = pos === "QB" ? 0.4 : pos === "TE" ? 0.8 : (pos === "K" || pos === "DEF") ? 0.2 : 1.0;
        let score = 1000 - (player.adp * 2.0) + (player.proj_pts * posWeight * 3.5);

        // Backup penalties (aligned with fantasy_league_sim BACKUP_PENALTY)
        if (pos === "QB" && qbCount >= 1) {
          score -= 350 * qbCount;
        } else if (pos === "TE" && teCount >= 1) {
          score -= 250 * teCount;
        } else if (pos === "RB" && rbCount >= 4) {
          score -= 60 * (rbCount - 3);
        } else if (pos === "WR" && wrCount >= 4) {
          score -= 60 * (wrCount - 3);
        }

        // Starter urgency
        if (pos === "TE" && teCount === 0 && currentRound >= 6) score += 250;
        if (pos === "QB" && qbCount === 0 && currentRound >= 5) score += 250;
        if (pos === "WR" && wrCount < starterTargets.WR && currentRound >= 4) score += 200;
        if (pos === "RB" && rbCount < starterTargets.RB && currentRound >= 4) score += 200;

        // Strategy / Archetype Biases
        if (persona === "ZERO_RB_ZEALOT" || persona === "ZERO_RB") {
          if (currentRound <= 4 && pos === "RB") {
            score -= 400;
          } else if (currentRound >= 5 && currentRound <= 7 && pos === "RB") {
            score += 200;
          } else if (currentRound <= 4 && (pos === "WR" || pos === "TE" || pos === "QB")) {
            score += 150;
          }
        } else if (persona === "HERO_RB") {
          if (currentRound <= 2 && rbCount === 0 && pos === "RB") {
            score += 300;
          } else if (currentRound >= 3 && currentRound <= 6 && rbCount >= 1 && pos === "RB") {
            score -= 350;
          }
        } else if (persona === "RB_HEAVY") {
          if (currentRound <= 4 && pos === "RB") {
            if (rbCount < 2) score += 280;
            else if (rbCount === 2) score += 140;
          }
        } else if (persona === "EARLY_QB") {
          if (currentRound <= 3 && pos === "QB" && qbCount === 0) {
            score += 350;
          }
        } else if (persona === "EARLY_TE") {
          if (currentRound <= 3 && pos === "TE" && teCount === 0) {
            score += 300;
          }
        } else if (persona === "BPA" || persona === "ANALYTICS_NERD") {
          if (player.adp < currentOverall) {
            score += (currentOverall - player.adp) * 5.0;
          }
        } else if (persona === "TRASH_TALKER") {
          score += (Math.random() * 24 - 12);
          if (player.proj_pts > 220) score *= 1.15;
          if (currentRound <= 3 && (pos === "QB" || pos === "TE") && ((pos === "QB" && qbCount === 0) || (pos === "TE" && teCount === 0))) {
            score += 180;
          }
        } else if (persona === "WAIVER_HAWK") {
          if (pos === "WR" || pos === "RB") score += (player.proj_avg || 10) * 2.5;
          if (currentRound >= 9 && (pos === "RB" || pos === "WR")) score *= 1.2;
        } else if (persona === "DIE_HARD") {
          if (currentRound <= 4 && pos === "RB") {
            if (rbCount < 2) score += 200;
          }
          if (player.age >= 26) score *= 1.15;
          score += (Math.random() * 12 - 6);
        } else if (persona === "TRADE_FIEND") {
          if (player.adp < currentOverall) {
            score += (currentOverall - player.adp) * 4.5;
          }
          if (player.proj_pts > 200) score *= 1.12;
        } else if (persona === "UNDERDOG") {
          if (player.age <= 24) score *= 1.25;
          score += (Math.random() * 16 - 4);
        }

        if (score > bestScore) {
          bestScore = score;
          bestPlayer = player;
        }
      }

      return bestPlayer;
    },
    [availablePlayers, draftState.picks, draftState.current_pick_index, draftState.teams]
  );

  // Single Simulation Step (AI or Auto-Pick)
  const handleSimStep = useCallback(() => {
    if (draftState.status !== "IN_PROGRESS") return;
    if (draftState.current_pick_index >= draftState.picks.length) return;

    const currentP = draftState.picks[draftState.current_pick_index];
    const isUserPicking = currentP.team_slot === draftState.user_slot;

    if (isUserPicking) {
      if (draftState.auto_pick) {
        // Find top queued player or top ADP player
        const queuedAvailable = availablePlayers.filter(
          (p) => !p.is_drafted && queuedIds.includes(p.player_id)
        );
        const playerToDraft = queuedAvailable[0] || selectAiPlayer();
        if (playerToDraft) {
          executePick(playerToDraft);
        }
      } else {
        // Pauses sim because user must pick manually
        setIsSimulatingAll(false);
      }
    } else {
      // AI Team Turn
      const playerToDraft = selectAiPlayer();
      if (playerToDraft) {
        executePick(playerToDraft);
      }
    }
  }, [draftState, availablePlayers, queuedIds, selectAiPlayer, executePick]);

  // Simulation Interval Engine
  useEffect(() => {
    if (draftState.status !== "IN_PROGRESS") return;
    if (draftState.current_pick_index >= draftState.picks.length) return;

    const currentP = draftState.picks[draftState.current_pick_index];
    const isUserPicking = currentP.team_slot === draftState.user_slot;

    if (!isUserPicking || draftState.auto_pick || isSimulatingAll) {
      const timer = setTimeout(() => {
        handleSimStep();
      }, isSimulatingAll ? 100 : simSpeedMs);
      return () => clearTimeout(timer);
    }
  }, [
    draftState.status,
    draftState.current_pick_index,
    draftState.user_slot,
    draftState.auto_pick,
    isSimulatingAll,
    simSpeedMs,
    handleSimStep,
  ]);

  // Sim To My Turn
  const handleSimToMyTurn = () => {
    if (draftState.status !== "IN_PROGRESS") return;
    setIsSimulatingAll(false);
    // Sim loop will run until user_slot turn is encountered
  };

  // Sim All Remaining Picks
  const handleSimAll = () => {
    if (draftState.status === "NOT_STARTED") {
      setDraftState((prev) => ({ ...prev, status: "IN_PROGRESS", auto_pick: true }));
    } else {
      setDraftState((prev) => ({ ...prev, auto_pick: true }));
    }
    setIsSimulatingAll(true);
  };

  // Queue Operations
  const handleToggleQueue = (player: NapperDraftPlayer) => {
    setQueuedIds((prev) =>
      prev.includes(player.player_id)
        ? prev.filter((id) => id !== player.player_id)
        : [...prev, player.player_id]
    );
  };

  const handleRemoveFromQueue = (playerId: number) => {
    setQueuedIds((prev) => prev.filter((id) => id !== playerId));
  };

  const handleMoveQueueItem = (index: number, direction: "UP" | "DOWN") => {
    setQueuedIds((prev) => {
      const copy = [...prev];
      const targetIdx = direction === "UP" ? index - 1 : index + 1;
      if (targetIdx < 0 || targetIdx >= copy.length) return prev;
      const temp = copy[index];
      copy[index] = copy[targetIdx];
      copy[targetIdx] = temp;
      return copy;
    });
  };

  // Chat message submission
  const handleSendChatMessage = (text: string) => {
    const newMsg: NapperDraftComment = {
      id: `chat-${Date.now()}`,
      sender_name: "The Straw Hat Pirates",
      sender_handle: "@jrereyi2000",
      message: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
    setComments((c) => [newMsg, ...c]);
  };

  // Restart Mock Draft
  const handleRestartMock = () => {
    setRecapModalOpen(false);
    setIsSimulatingAll(false);
    setAvailablePlayers((prev) => prev.map((p) => ({ ...p, is_drafted: false })));
    setQueuedIds([]);
    const newSlot = Math.floor(Math.random() * draftState.teams_count) + 1;
    const { teams: resetTeams, picks: resetPicks } = buildMockTeamsAndPicks(
      draftState.teams_count,
      draftState.rounds_count,
      newSlot,
      archetypeComposition,
      true
    );
    setDraftState((prev) => ({
      ...prev,
      status: "NOT_STARTED",
      user_slot: newSlot,
      current_pick_index: 0,
      auto_pick: false,
      teams: resetTeams,
      picks: resetPicks,
    }));
  };

  // Top Recommendation from Assistant
  const topAssistantRecommendation = availablePlayers.find((p) => !p.is_drafted);

  const queuedPlayersList = queuedIds
    .map((id) => availablePlayers.find((p) => p.player_id === id))
    .filter((p): p is NapperDraftPlayer => Boolean(p && !p.is_drafted));

  return (
    <div
      className={`napper-draft-container ${presentationMode ? "fixed inset-0 z-50 bg-[#090e17]" : ""}`}
    >
      {/* 1. Header Bar */}
      <header className="draft-header-bar">
        {/* Left: Back button, Football Hex Shield, Title & Subtitle */}
        <div className="draft-header-left">
          <button
            type="button"
            onClick={onBack}
            className="draft-back-btn"
            title="Return to Napper League"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
          </button>

          <div className="draft-shield-badge">
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              <circle cx="12" cy="11" r="3" />
            </svg>
          </div>

          <div className="draft-title-box">
            <h1 className="draft-main-title">{draftState.title}</h1>
            <div className="draft-sub-title">
              Mock Draft • {draftState.teams_count} Teams • PPR • {draftState.rounds_count} Rounds • Slot {draftState.user_slot}
            </div>
          </div>
        </div>

        {/* Center: Assistant Potion, START DRAFT, Fast Forward Sim Controls */}
        <div className="draft-header-actions">
          {/* Potion / Draft Assistant Icon */}
          <button
            type="button"
            onClick={() => setAssistantModalOpen(true)}
            className="draft-potion-btn"
            title="Open Draft Assistant"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M10 2v7.31L4.69 17.5a2 2 0 0 0 1.62 2.5h11.38a2 2 0 0 0 1.62-2.5L14 9.31V2" />
              <line x1="8.5" y1="2" x2="15.5" y2="2" />
              <line x1="14" y1="9.3" x2="10" y2="9.3" />
            </svg>
          </button>

          {/* Start Draft or Auto-Pick Button */}
          {draftState.status === "NOT_STARTED" ? (
            <button
              type="button"
              onClick={() => setDraftState((prev) => ({ ...prev, status: "IN_PROGRESS" }))}
              className="draft-start-pill-btn"
            >
              <span>⚡ START DRAFT</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() =>
                setDraftState((prev) => ({ ...prev, auto_pick: !prev.auto_pick }))
              }
              className={`draft-sim-pill-btn ${draftState.auto_pick ? "active" : ""}`}
              title="Automatically drafts top queued or highest ADP player"
            >
              <span>AUTO PICK: {draftState.auto_pick ? "ON" : "OFF"}</span>
            </button>
          )}

          {/* Fast-Forward Simulation Controls */}
          {draftState.status === "IN_PROGRESS" && (
            <>
              <button
                type="button"
                onClick={handleSimStep}
                className="draft-sim-pill-btn"
                title="Simulate Current Pick"
              >
                <span>Sim Pick</span>
              </button>

              <button
                type="button"
                onClick={handleSimToMyTurn}
                className="draft-sim-pill-btn"
                title="Simulate until it is your turn to pick"
              >
                <span>Sim To My Turn</span>
              </button>
            </>
          )}

          {draftState.status !== "COMPLETED" && (
            <button
              type="button"
              onClick={handleSimAll}
              className="draft-sim-pill-btn"
              title="Fast forward entire draft"
            >
              <span>Sim All</span>
            </button>
          )}

          {draftState.status === "COMPLETED" && (
            <button
              type="button"
              onClick={() => setRecapModalOpen(true)}
              className="draft-start-pill-btn"
            >
              <span>View Report Card</span>
            </button>
          )}
        </div>

        {/* Right: Sound toggle, Theme toggle, Fullscreen, Settings */}
        <div className="draft-header-right">
          {/* Sound Toggle */}
          <button
            type="button"
            onClick={() => setSoundEnabled(!soundEnabled)}
            className={`draft-util-btn ${soundEnabled ? "text-[#00ceb8]" : "text-[#64748b]"}`}
            title={soundEnabled ? "Mute audio cues" : "Unmute audio cues"}
          >
            {soundEnabled ? (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <line x1="23" y1="9" x2="17" y2="15" />
                <line x1="17" y1="9" x2="23" y2="15" />
              </svg>
            )}
          </button>

          {/* Presentation / Fullscreen Toggle */}
          <button
            type="button"
            onClick={() => setPresentationMode(!presentationMode)}
            className={`draft-util-btn ${presentationMode ? "active" : ""}`}
            title="Toggle Big Screen Mode"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
          </button>

          {/* Settings Speed Dropdown */}
          <select
            value={simSpeedMs}
            onChange={(e) => setSimSpeedMs(Number(e.target.value))}
            className="draft-util-btn px-1 text-[10px] font-bold outline-none cursor-pointer"
            title="Simulation Pace"
          >
            <option value={800}>Normal</option>
            <option value={300}>Fast</option>
            <option value={80}>Turbo</option>
          </select>
        </div>
      </header>

      {/* 1b. Mock Draft Composition Toolbar (Visible when NOT_STARTED) */}
      {draftState.status === "NOT_STARTED" && (
        <div className="draft-config-bar">
          {/* Teams count pills */}
          <div className="draft-config-group">
            <span className="draft-config-label">Teams:</span>
            {[8, 10, 12].map((cnt) => (
              <button
                key={`team-pill-${cnt}`}
                type="button"
                onClick={() => handleTeamCountChange(cnt)}
                className={`draft-config-pill ${draftState.teams_count === cnt ? "active" : ""}`}
                title={`Configure a ${cnt}-team draft`}
              >
                {cnt} Teams
              </button>
            ))}
          </div>

          {/* Scoring Format: Strictly PPR only */}
          <div className="draft-config-group">
            <span className="draft-config-label">Scoring:</span>
            <span
              className="draft-config-pill active cursor-default"
              title="Official Project Mu scoring format: 1.0 Point Per Reception"
            >
              PPR (1.0)
            </span>
            <span
              className="draft-config-pill text-[#00ceb8] cursor-default text-[10px]"
              title="Superflex active (QB/RB/WR/TE in Flex 2)"
            >
              SUPERFLEX
            </span>
          </div>

          {/* Rounds count pills */}
          <div className="draft-config-group">
            <span className="draft-config-label">Rounds:</span>
            {[12, 14, 15, 16].map((rnd) => (
              <button
                key={`round-pill-${rnd}`}
                type="button"
                onClick={() => handleRoundsChange(rnd)}
                className={`draft-config-pill ${draftState.rounds_count === rnd ? "active" : ""}`}
                title={`Draft length: ${rnd} rounds`}
              >
                {rnd} Rds
              </button>
            ))}
          </div>

          {/* Archetypes Composition Selector */}
          <div className="draft-config-group">
            <span className="draft-config-label">Archetypes:</span>
            <select
              value={archetypePresetName}
              onChange={(e) => {
                const pName = e.target.value;
                if (pName === "Custom") {
                  setArchetypeModalOpen(true);
                } else {
                  const p = ARCHETYPE_PRESETS.find((x) => x.name === pName);
                  if (p) {
                    handleApplyArchetypes(p.getCounts(draftState.teams_count - 1), pName);
                  }
                }
              }}
              className="draft-config-select font-bold"
              title="Select archetype preset or customize AI composition"
            >
              {ARCHETYPE_PRESETS.map((p) => (
                <option key={`opt-preset-${p.name}`} value={p.name}>
                  {p.name}
                </option>
              ))}
              <option value="Custom">Custom...</option>
            </select>
            <button
              type="button"
              onClick={() => setArchetypeModalOpen(true)}
              className="draft-config-pill active flex items-center gap-1 text-[10px]"
              title="Open full archetype composition editor"
            >
              <span>⚙ Customize</span>
            </button>
          </div>

          {/* User Draft Slot selector & Randomize Order Button */}
          <div className="draft-config-group">
            <span className="draft-config-label">Your Slot:</span>
            <select
              value={draftState.user_slot}
              onChange={(e) => handleClaimSlot(Number(e.target.value))}
              className="draft-config-select font-black text-[#00ceb8]"
              title="Select your team's draft slot"
            >
              {Array.from({ length: draftState.teams_count }, (_, i) => i + 1).map((s) => (
                <option key={`opt-slot-${s}`} value={s}>
                  Slot {s} {s === draftState.user_slot ? "(YOU)" : ""}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => handleRandomizeOrder(false)}
              className="draft-config-pill flex items-center gap-1 hover:border-[#00ceb8] hover:text-[#00ceb8]"
              title="Randomize both user draft spot and opponent draft order"
            >
              <span>🎲 Randomize Order</span>
            </button>
            <button
              type="button"
              onClick={() => setOrderModalOpen(true)}
              className="draft-config-pill flex items-center gap-1 hover:border-[#00ceb8] hover:text-[#00ceb8]"
              title="Manually customize and set draft slot order for all teams"
            >
              <span>↕ Set Order</span>
            </button>
          </div>
        </div>
      )}

      {/* 2. Top Half: Snaking Draft Board Grid Matrix */}
      <DraftBoardGrid
        draftState={draftState}
        onClaimSlot={handleClaimSlot}
        onOpenSetOrder={() => setOrderModalOpen(true)}
        onSelectPick={(p) => {
          if (p.player) setSelectedNotesPlayer(p.player);
        }}
        isSelectionBoxOpen={isSelectionBoxOpen}
        onToggleSelectionBox={() => setIsSelectionBoxOpen((prev) => !prev)}
        onOpenSelectionBox={() => setIsSelectionBoxOpen(true)}
        onCloseSelectionBox={() => setIsSelectionBoxOpen(false)}
        isUserTurn={isUserTurn}
      />

      {/* 3. Bottom Half: Split View (Player Pool Left, Roster/Queue/Chat Right) */}
      <div className={`draft-bottom-split ${!isSelectionBoxOpen ? "collapsed" : ""}`}>
        {/* Left: Player Pool Table & Filter Bar */}
        <DraftPlayerPool
          players={availablePlayers}
          userRoster={userRoster}
          queueIds={queuedIds}
          isUserOnClock={isUserTurn}
          onDraftPlayer={(player) => executePick(player)}
          onToggleQueue={handleToggleQueue}
          onSelectPlayerNotes={(player) => setSelectedNotesPlayer(player)}
        />

        {/* Right: Sleeper Roster / Queue / Chat Tabs */}
        <DraftRosterQueue
          teams={draftState.teams}
          userSlot={draftState.user_slot}
          queuedPlayers={queuedPlayersList}
          comments={comments}
          isUserOnClock={isUserTurn}
          onDraftPlayer={(player) => executePick(player)}
          onRemoveFromQueue={handleRemoveFromQueue}
          onMoveQueueItem={handleMoveQueueItem}
          onSendChatMessage={handleSendChatMessage}
        />
      </div>

      {/* 4. Scouting Notes Modal */}
      {selectedNotesPlayer && (
        <div className="draft-modal-overlay" onClick={() => setSelectedNotesPlayer(null)}>
          <div
            className="bg-[#0e1626] border border-[#233758] rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-[#1f2d45] pb-3">
              <div className="flex items-center gap-3">
                <img
                  src={selectedNotesPlayer.headshot_url}
                  alt={selectedNotesPlayer.name}
                  className="w-12 h-12 rounded-full border border-[#2b3e60] object-cover bg-[#090e17]"
                  onError={(e) => {
                    e.currentTarget.src = "/static/players/robot_avatar.webp";
                  }}
                />
                <div>
                  <h3 className="text-base font-extrabold text-white">
                    {selectedNotesPlayer.name}
                  </h3>
                  <div className="text-xs text-[#8a99ad]">
                    {selectedNotesPlayer.position} • {selectedNotesPlayer.team} (Bye {selectedNotesPlayer.bye})
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedNotesPlayer(null)}
                className="text-[#64748b] hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="text-xs text-[#cbd5e1] leading-relaxed">
              {selectedNotesPlayer.notes || "High impact contributor with solid weekly volume."}
            </div>

            <div className="grid grid-cols-3 gap-2 bg-[#141f33] p-3 rounded-xl text-center">
              <div>
                <div className="text-[10px] text-[#64748b] font-bold">ADP</div>
                <div className="text-sm font-black text-[#00ceb8]">
                  {selectedNotesPlayer.adp.toFixed(1)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-[#64748b] font-bold">PROJ PTS</div>
                <div className="text-sm font-black text-white">
                  {selectedNotesPlayer.proj_pts.toFixed(1)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-[#64748b] font-bold">AGE</div>
                <div className="text-sm font-black text-[#94a3b8]">
                  {selectedNotesPlayer.age.toFixed(1)}
                </div>
              </div>
            </div>

            {isUserTurn && !selectedNotesPlayer.is_drafted && (
              <button
                type="button"
                onClick={() => {
                  executePick(selectedNotesPlayer);
                  setSelectedNotesPlayer(null);
                }}
                className="w-full py-2.5 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-black uppercase tracking-wider transition-all"
              >
                Draft {selectedNotesPlayer.name} Now
              </button>
            )}
          </div>
        </div>
      )}

      {/* 5. Assistant Tooltip Modal */}
      {assistantModalOpen && (
        <div className="draft-modal-overlay" onClick={() => setAssistantModalOpen(false)}>
          <div
            className="bg-[#0e1626] border border-[#233758] rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-[#1f2d45] pb-3">
              <div className="flex items-center gap-2">
                <span className="text-xl">🧪</span>
                <h3 className="text-base font-extrabold text-white">
                  Draft Assistant Recommendation
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setAssistantModalOpen(false)}
                className="text-[#64748b] hover:text-white"
              >
                ✕
              </button>
            </div>

            {topAssistantRecommendation ? (
              <div className="space-y-3">
                <p className="text-xs text-[#8a99ad]">
                  Based on optimal value over replacement (VORP) and your current roster composition:
                </p>
                <div className="p-3 rounded-xl bg-[#14233a] border border-[#00ceb8]/40 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <img
                      src={topAssistantRecommendation.headshot_url}
                      alt={topAssistantRecommendation.name}
                      className="w-10 h-10 rounded-full object-cover bg-[#090e17]"
                    />
                    <div>
                      <div className="text-xs font-black text-white">
                        {topAssistantRecommendation.name}
                      </div>
                      <div className="text-[10px] text-[#00ceb8]">
                        {topAssistantRecommendation.position} • {topAssistantRecommendation.team} (ADP #{topAssistantRecommendation.adp_rank})
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-mono font-bold text-white">
                      {topAssistantRecommendation.proj_pts.toFixed(1)}
                    </div>
                    <div className="text-[9px] text-[#64748b]">PROJ PTS</div>
                  </div>
                </div>

                {isUserTurn && (
                  <button
                    type="button"
                    onClick={() => {
                      executePick(topAssistantRecommendation);
                      setAssistantModalOpen(false);
                    }}
                    className="w-full py-2.5 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-black uppercase tracking-wider"
                  >
                    Draft Recommended Player
                  </button>
                )}
              </div>
            ) : (
              <p className="text-xs text-[#64748b]">No more players available in draft pool.</p>
            )}
          </div>
        </div>
      )}

      {/* 6. Post-Draft Recap Modal */}
      {recapModalOpen && (
        <DraftRecapModal
          draftState={draftState}
          onClose={() => setRecapModalOpen(false)}
          onRestartMock={handleRestartMock}
          onSaveAsLeague={(title) => {
            if (onLeagueCreated) {
              onLeagueCreated(Date.now());
            } else {
              alert(`Draft saved! League "${title}" created.`);
              onBack();
            }
          }}
        />
      )}

      {/* 7. Mock Draft Archetype Composition Modal */}
      <ArchetypeCompositionModal
        isOpen={archetypeModalOpen}
        onClose={() => setArchetypeModalOpen(false)}
        teamsCount={draftState.teams_count}
        currentComposition={archetypeComposition}
        onApply={handleApplyArchetypes}
      />

      {/* 8. Customize Draft Order Modal */}
      <DraftOrderModal
        isOpen={orderModalOpen}
        onClose={() => setOrderModalOpen(false)}
        teams={draftState.teams}
        onApplyOrder={handleApplyDraftOrder}
      />
    </div>
  );
};

export default NapperDraftRoom;
