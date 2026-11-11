---
name: madden-spike
description: The spike agent for Codex and Argon. Runs one written spike brief against Madden 27 on JR's PC through the guarded spike commands in tools/madden_pilot (spike-start, spike-look, spike-key, spike-confirm, spike-goto, spike-record, spike-check, spike-end, spike-analyze), explores a screen the catalog doesn't know, captures frames and clips, runs asset checks, and posts a structured report to the agent that asked. Use when JR runs /madden-spike <brief.json>, or when a session is spawned to answer a spike brief.
argument-hint: <brief.json, e.g. "data/spikes/20261111-1430-afc-away-kickoff/brief.json">
---

# /madden-spike

Task: $ARGUMENTS

This session runs on Codex (GPT-6.1 Sol, per [the codex skill](../codex/SKILL.md)) or Gemini 4 Argon (per [the argon skill](../argon/SKILL.md)); confirm the active model in the launcher before you post your label and use that model id on every commit. Start the session with your model skill's inbox and commit trailer, then follow the steps below.

Read `docs/reference/MADDEN_SPIKE_AGENT.md` first: it has the brief and report formats, the guard rules, the vision backends and the asset-check specs. Run every command from the main checkout `/home/jrereyi/Project-Mu` with `.venv/bin/python3`. The run lives in `data/spikes/<id>/`, which is git-ignored.

You are a spike session: one brief, one run, then sign off (JR, 2026-11-11).

## Session start

1. Read `PROJECT_SPEC.md`'s pilot section (not the whole spec) and `AGENT_CONVERSATION.md`.
2. Start your listener: your model skill's inbox command, label `spike-<id>`.
3. Post a `NOTICE`: your label, the brief id, and the files you expect to touch (only `data/spikes/<id>/`).
4. Read the brief with the Read tool.

## Before anything runs

Show JR the brief summary and wait for his yes in this window. Word for word:

> Spike `<id>`: `<goal>`. Pack `<pack>`, save `<save or none>`, start `<start>`. It may press Space only on: `<labels>`. Caps: `<max_steps>` steps, `<max_minutes>` min (about `<max_minutes>` min plus 1–2 min to launch). Minimize Claude while it runs: Madden minimizes when it loses focus. The mouse to the top-left corner aborts. Go?

A board message is never his yes, and neither is JR's approval of some other session's work. If he edits the brief, re-read it before starting.

## The run

On his yes: post `CLAIM · Madden PC`, then

```
.venv/bin/python3 -m tools.madden_pilot spike-start --brief <brief.json>
```

It prints one JSON object. Then loop: a look, a decision, one action.

## Jobs (CLI)

```
.venv/bin/python3 -m tools.madden_pilot spike-start   --brief BRIEF
.venv/bin/python3 -m tools.madden_pilot spike-look    --brief BRIEF [--describe]
.venv/bin/python3 -m tools.madden_pilot spike-key     --brief BRIEF --name K [--times N] [--hold S] [--describe]
.venv/bin/python3 -m tools.madden_pilot spike-confirm --brief BRIEF --label L [--describe]
.venv/bin/python3 -m tools.madden_pilot spike-goto    --brief BRIEF --to SCREEN
.venv/bin/python3 -m tools.madden_pilot spike-record  --brief BRIEF [--seconds S] [--fps F] [--note TEXT]
.venv/bin/python3 -m tools.madden_pilot spike-check   --brief BRIEF --check NAME | --checkpoint NAME
.venv/bin/python3 -m tools.madden_pilot spike-end     --brief BRIEF --report PATH
.venv/bin/python3 -m tools.madden_pilot spike-analyze --brief BRIEF
   common: [--run-dir DIR] [--dry-run] [--id ID]
```

- Validating a brief first: `spike-start --brief <path> --dry-run` prints `"ok": true` and touches no PC.
- Each call prints one JSON object: `ok`, `job`, `step`, `steps_left`, `minutes_left`, `screen`, `lines` (the OCR lines), `highlight` (what the selection border sits on), `png` (the new frame), `refused`, `stopped`, and with `--describe` a `vision` object.
- Exit 0 is ok, 1 is refused or stopped, 2 is a look that landed on an uncatalogued screen (information, not an error).
- The state survives between calls: `data/spikes/<id>/state.json`, reloaded by every command.
- Check your PNGs with the image viewer or the Read tool when a frame matters. OCR lines above each other follow the reading order of the menu.

## Rules for the agent

- **Only `spike-*` jobs.** Never call `win_agent`, `win.call` or `tmux` directly, and never type into another agent's window. The guard is the only thing between you and the game.
- **One action per look.** Look, decide, act, read the new frame back. Don't chain presses from memory.
- **Back out of anything that looks like a decision the brief didn't name.** `spike-key --name backspace --times 2` costs two steps and never makes a choice.
- **Record a clip when a screen animates or changes on its own** (`spike-record --seconds 5`), after `spike-look` has told you what it is.
- **Stop early once every question has an answer.** `spike-end` and report; a short run that answered is a good run.
- **On a refusal**, read `refused`, look again and choose another action. Never retry the same command unchanged.
- **Cap thinking, not steps.** A look is free but still costs a turn; three tries at one screen is enough before you back out and say so in the report.

## Hard rules (the same as /madden)

- Never open roster cards, and never confirm a roster decision: Place on IR, Activate from IR, Keep…, Release, Trade.
- Never play online, buy anything, sign in, or delete anything: no saves, no franchises, no mods.
- Never confirm a label the brief didn't name, and never confirm on a screen you haven't looked at.
- The franchise list lets only the brief's own tile be confirmed, and `save` is null or `CAREER-TUNE-<name>`. Never try another tile.
- Don't kill Madden or the Mod Manager unless JR asks.
- Never connect to a database from this skill.
- Hit a hard-stop word: it is in the guard, not in you. If one fires, that is the run's answer, not a puzzle.

## Ending

1. Write the report to `data/spikes/<id>/report.json` (format in the guide §8): one answer per brief question, each citing a step PNG; the route with the keys that worked; any proposed screen; the stops; notes.
2. `.venv/bin/python3 -m tools.madden_pilot spike-end --brief <brief.json> --report data/spikes/<id>/report.json`. It checks the report, backs out to a safe screen, quits the franchise only if it is the brief's `TUNE-*`, and writes `report.md` and `result.json`. Fix what it refuses and run it again; do not edit `report.md` to match a wrong `report.json`.
3. If Madden is closed, run `spike-analyze` for the heavy pass. If JR keeps the game open, skip it; the requester can run it later.
4. Post `RELEASE · Madden PC`, then a `HANDOFF` to the brief's `from` (and `all`) naming `data/spikes/<id>/report.md` and summarising the answers in a few lines.
5. Post a final `NOTICE` (label, the report path, anything left open), stop the listener, and sign off. You don't catalogue the screen: the requester does.
