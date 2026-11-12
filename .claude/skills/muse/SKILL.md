---
name: muse
description: Start a Project Mu board session for a Muse model (Muse Spark 1.3 on OpenCode Go, or Muse Ember 1.0 on OpenCode Zen) in OpenCode, and apply its work limits, computer-use rules and commit attribution. Use when a Muse route is active.
---

# Muse session

Start in Project Mu with `tools/muse_session.sh`, or use `tools/spawn_agent.sh <work-label> muse` for the tmux workflow. It runs OpenCode on a Muse model chosen by `MU_MUSE_MODEL`: `opencode-go/muse-spark-1.3-contributor` (Muse Spark 1.3 on JR's OpenCode Go plan, the benchmark's first step) by default, or `MU_MUSE_MODEL=opencode/muse-ember-1.0 tools/muse_session.sh` for Muse Ember 1.0 on OpenCode Zen (the second step, only after the first clears its gate). The launcher prints the route line and passes `--model` itself, because the project pin in `.opencode/opencode.json` is DeepSeek. Confirm `MU_AGENT_MODEL`: `muse-spark-1.3-contributor`, or `muse-ember-1.0` for step 2. Any other value means the project pin won and a DeepSeek model is answering: stop and tell JR rather than sign as the wrong model. No key lives in the repo or the wrapper; OpenCode uses its own stored Console credential.

Follow [Model inboxes](../../../docs/reference/AGENTS.md#model-inboxes) with inbox `muse`: `python3 -m tools.agent_board_inbox muse --label <work-label>` for the startup snapshot, then the same command with `--watch` as the session's monitor. Sign board messages `from: <MU_AGENT_MODEL> (<work-label>)` and end every authorized commit this session makes with `Model: <MU_AGENT_MODEL>`. Both read the value the launcher exported; never sign as a model it did not report.

**Work limits (JR, 2026-11-12, D9).** A Muse session may run `/builder` and computer-use work. It may not run `/propose`, `/execute-plan` or `/audit`, and it may not spawn another agent session: ask JR instead.

**Data (R7).** On `muse-spark-*-contributor`, OpenCode states the model trains on request data, so repo source, plan text and pilot frames go to a training endpoint. Say so in the startup NOTICE, and keep builder work to what JR has approved for that route: computer-use frames for the benchmark, with no repo-wide builder work on Spark until JR says otherwise. Ember requests no training endpoint; prefer it for builder work once JR approves.

**Computer use.** The only path is the [`/madden`](../madden/SKILL.md) skill: read it and follow its hard rules exactly. Ask JR for the PC every time, take `CLAIM · Madden PC` on the board first, run `--dry-run` before every job, use `CAREER-TUNE-*` saves only, never open a roster card, and never confirm on a screen the catalog does not hold. The session drives `tools/madden_pilot` from its own shell and reads stop screenshots with the Read tool. It never invents new automation, and never presses a key outside the catalog's exit list.

**Hand-over.** Run `/context-check` at 60%, as builders do: post the hand-over NOTICE, RELEASE your claims, stop the listener and stop.
