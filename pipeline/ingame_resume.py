"""
pipeline/ingame_resume.py: Reader for Madden 27's INGAME-RESUMEDATA, the resume file Madden rewrites
after every play of a game in progress (docs/in_progress/PC_MIGRATION_PLAN.md §9.2).

The file is an FBCHUNKS header (magic, sizes, write time, build, mode such as "Ultimate Career" or
"Exhibition") followed by an EA Blaze TDF tree: each field is a 3-byte tag (four 6-bit characters,
e.g. QRTR, DOWN, YARD) and a type byte, then the value. Integers are Blaze varints (6 bits and a sign
in the first byte, then 7 bits a byte). Read-only: the file is copied into memory and never written.

Usage:
  python3 -m pipeline.ingame_resume [FILE] [--tree] [--json]

  FILE     INGAME-RESUMEDATA path (default: MADDEN_SAVES_DIR/INGAME-RESUMEDATA)
  --tree   Print the whole decoded tree (tags and values) instead of the summary
  --json   Print the summary (or the tree with --tree) as JSON
"""
import argparse
import datetime
import json
import os
import struct
import sys

from pipeline.notify import env

MAGIC = b"FBCHUNKS"
PAYLOAD_OFFSET = 0x142  # header (0x130) plus the 18-byte chunk header; the TDF tree starts here
NO_ID = 1048575  # 0xFFFFF: empty player-id slot


class ResumeFormatError(ValueError):
    pass


def tag_name(b0, b1, b2):
    v = (b0 << 16) | (b1 << 8) | b2
    return "".join(chr(((v >> s) & 0x3F) + 0x20) for s in (18, 12, 6, 0)).rstrip()


class _Reader:
    def __init__(self, data, pos):
        self.b, self.i = data, pos

    def byte(self):
        if self.i >= len(self.b):
            raise ResumeFormatError("unexpected end of data")
        x = self.b[self.i]
        self.i += 1
        return x

    def varint(self):
        x = self.byte()
        neg, v, shift = x & 0x40, x & 0x3F, 6
        while x & 0x80:
            x = self.byte()
            v |= (x & 0x7F) << shift
            shift += 7
        return -v if neg else v

    def value(self, typ):
        if typ == 0:
            return self.varint()
        if typ == 1:  # string: length (with the NUL), bytes
            n = self.varint()
            s = self.b[self.i:self.i + n]
            self.i += n
            return s.rstrip(b"\x00").decode("utf-8", "replace")
        if typ == 2:  # blob
            n = self.varint()
            s = self.b[self.i:self.i + n]
            self.i += n
            return s.hex()
        if typ == 3:
            return self.struct()
        if typ == 4:  # list: element type, count, elements
            et, n = self.byte(), self.varint()
            return [self.value(et) for _ in range(n)]
        if typ == 5:  # map: key type, value type, count, pairs
            kt, vt, n = self.byte(), self.byte(), self.varint()
            out = {}
            for _ in range(n):
                k = self.value(kt)
                out[k] = self.value(vt)
            return out
        if typ == 6:  # union: active member (0x7f = none), then one tagged field
            active = self.byte()
            if active == 0x7F:
                return None
            name, v = self.field()
            return {"_member": active, name: v}
        if typ == 7:  # integer list
            return [self.varint() for _ in range(self.varint())]
        if typ == 8:  # object type: component, type
            return [self.varint(), self.varint()]
        if typ == 9:  # object id: component, type, id
            return [self.varint(), self.varint(), self.varint()]
        if typ == 0x0A:  # float
            v = struct.unpack(">f", self.b[self.i:self.i + 4])[0]
            self.i += 4
            return v
        raise ResumeFormatError(f"unknown TDF type {typ:#x} at {self.i - 1:#x}")

    def field(self):
        b0, b1, b2, typ = self.byte(), self.byte(), self.byte(), self.byte()
        return tag_name(b0, b1, b2), self.value(typ)

    def struct(self):
        """Fields until a 0 byte. A tag repeated within one struct keeps every value, in a list."""
        out = {}
        while True:
            if self.b[self.i] == 0:
                self.i += 1
                return out
            name, v = self.field()
            if name in out:
                if not isinstance(out[name], _Repeated):
                    out[name] = _Repeated([out[name]])
                out[name].append(v)
            else:
                out[name] = v


class _Repeated(list):
    pass


def read_header(data):
    if data[:8] != MAGIC:
        raise ResumeFormatError("not an FBCHUNKS file")
    year, month, day, hour, minute, second = struct.unpack_from("<6H", data, 0x16)
    build = data[0x22:0x3E].split(b"\x00")[0].decode("ascii", "replace")
    mode = data[0x42:0x82].split(b"\x00")[0].decode("ascii", "replace")
    return {
        "written": datetime.datetime(year, month, day, hour, minute, second).isoformat(),
        "build": build,
        "mode": mode,
    }


def read_tree(data):
    """The TDF tree as {tag: value}; fields at the top level run until the data ends or zero padding."""
    r = _Reader(data, PAYLOAD_OFFSET)
    out = {}
    while r.i < len(data) and data[r.i] != 0:
        name, v = r.field()
        out[name] = v
    return out


def read_file(path):
    with open(path, "rb") as f:
        data = f.read()
    return read_header(data), read_tree(data)


# Player stat codes (HSTT/ASTT PLST), matched to the plays of a played Panthers–Falcons game on 2026-10-30.
# Codes not listed here are kept under their raw PSnn name.
PLAYER_STATS = {
    "PS03": "pass_comp", "PS04": "pass_att", "PS05": "pass_yds", "PS07": "pass_td", "PS08": "pass_int",
    "PS10": "pass_long",
    "PS11": "rush_att", "PS12": "rush_yds", "PS17": "rush_yac", "PS18": "rush_long",
    "PS19": "rec", "PS20": "rec_yds", "PS22": "rec_td", "PS23": "rec_yac", "PS26": "rec_long",
    "PS29": "tackles", "PS30": "assists", "PS32": "tfl", "PS33": "sacks", "PS34": "def_int", "PS35": "int_ret_yds",
    "PS37": "int_ret_long",
    "PS44": "fgm", "PS45": "fga", "PS47": "fg_long", "PS61": "kickoffs",
    "PS84": "snaps",
}
SKIP_PLAYER_CODES = {"PS01", "PS85", "PS86"}  # player id, and two flags every listed player has
# Play record fields (PLRS); EMPTY values (255, NO_ID) mean "not involved".
PLAY_FIELDS = {
    "QRTR": "quarter", "CSEC": "clock", "DOWN": "down", "YDFS": "to_go", "LOSY": "line",
    "YARD": "yards", "PSKI": "passer_or_kicker", "RNID": "runner", "RCID": "receiver",
    "PTSC": "points", "DRNM": "drive",
}
EVENT_TYPES = {1: "interception", 2: "sack"}  # other EVTP values are reported by number


def _ids(x):
    return {int(k): v for k, v in (x or {}).items()}


def _clock(seconds):
    s = max(0, int(round(seconds or 0)))
    return f"{s // 60}:{s % 60:02d}"


def summarize(header, tree):
    """The game state at the last play: score, clock, down, plays, drives, player stat lines."""
    names, team_of = {}, {}
    for side in ("HOME", "AWAY"):
        for pid, p in _ids(tree.get(side, {}).get("ROST", {}).get("PINF")).items():
            names[pid] = f"{p.get('FRST', '')} {p.get('LAST', '')}".strip()
            team_of[pid] = side.lower()

    def who(pid):
        return names.get(pid, pid) if pid not in (None, 255, NO_ID) else None

    prog = tree.get("PROG", {})
    ball, gain_line = prog.get("BLSY"), prog.get("FDLN")
    state = {
        "quarter": prog.get("QRTR"),
        "clock": _clock(prog.get("GMCL")),
        "down": prog.get("DOWN") if (prog.get("DOWN") or 0) > 0 else None,
        "to_go": round(abs(gain_line - ball), 1) if (prog.get("DOWN") or 0) > 0 and ball is not None and gain_line is not None else None,
        "just_scored": bool(prog.get("JTSC")),
    }
    teams = {}
    for side, prog_key in (("home", "TGPI"), ("away", "TJFH")):
        info = prog.get(prog_key, {})
        teams[side] = {
            "team_id": tree.get(side.upper(), {}).get("TEID"),
            "score": info.get("PTSC", 0),
            "timeouts": info.get("TIME"),
            "challenges": info.get("CHAL"),
        }

    plays = []
    for _, p in sorted(_ids(tree.get("PLRS")).items()):
        row = {"play": p.get("PLNM", 0)}
        for code, label in PLAY_FIELDS.items():
            v = p.get(code)
            if v is None or v in (255, NO_ID):
                continue
            row[label] = who(v) if label in ("passer_or_kicker", "runner", "receiver") else v
        if "clock" in row:
            row["clock"] = _clock(row["clock"])
        plays.append(row)

    events = []
    for _, e in sorted(_ids(tree.get("EVTS")).items()):
        events.append({"play": e.get("PLNM"), "type": EVENT_TYPES.get(e.get("EVTP"), e.get("EVTP")),
                       "player": who(e.get("PLID")), "yards": e.get("YARD")})

    drives = []
    for _, dr in sorted(_ids(tree.get("DRIV")).items()):
        drives.append({"drive": dr.get("DRID"), "first_play": dr.get("PLNM", 0), "plays": dr.get("NMDP"),
                       "scorer": who(dr.get("SCP1")), "score_distance": dr.get("SPDS")})

    players = {"home": [], "away": []}
    for side, key in (("home", "HSTT"), ("away", "ASTT")):
        for pid, line in sorted(_ids(tree.get(key, {}).get("PLST")).items()):
            stats = {PLAYER_STATS.get(k, k): v for k, v in line.items() if k not in SKIP_PLAYER_CODES}
            if set(stats) - {"snaps"}:
                players[side].append({"player_id": pid, "name": names.get(pid), **stats})

    return {"file": header, "state": state, "teams": teams, "plays": plays, "events": events,
            "drives": drives, "players": players}


def format_summary(s):
    st, h, a = s["state"], s["teams"]["home"], s["teams"]["away"]
    down = f"  {st['down']} & {st['to_go']:g}" if st["down"] else ""
    out = [f"Written {s['file']['written']} ({s['file']['mode']})",
           f"Away {a['score']}  Home {h['score']}   Q{st['quarter']} {st['clock']}{down}"
           + ("   (just scored)" if st["just_scored"] else ""), "", "Plays:"]
    for p in s["plays"]:
        extras = {k: v for k, v in p.items() if k not in ("play", "quarter", "clock")}
        out.append(f"  {p['play']:>3}  Q{p.get('quarter', '?')} {p.get('clock', '')}  "
                   + "  ".join(f"{k}={v}" for k, v in extras.items()))
    if s["events"]:
        out += ["", "Events:"] + [f"  play {e['play']}: {e['type']} {e['player']}"
                                  + (f" {e['yards']} yds" if e["yards"] is not None else "") for e in s["events"]]
    for side in ("away", "home"):
        out += ["", f"{side.title()} players:"]
        for p in s["players"][side]:
            stats = {k: v for k, v in p.items() if k not in ("player_id", "name")}
            out.append(f"  {p['name'] or p['player_id']}: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read Madden's INGAME-RESUMEDATA")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = args.file or os.path.join(env("MADDEN_SAVES_DIR", "."), "INGAME-RESUMEDATA")
    header, tree = read_file(path)
    if args.tree:
        print(json.dumps({"header": header, "tree": tree}, indent=1, default=str))
    elif args.json:
        print(json.dumps(summarize(header, tree), indent=1, default=str))
    else:
        print(format_summary(summarize(header, tree)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
