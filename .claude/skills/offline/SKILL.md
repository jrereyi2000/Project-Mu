---
name: offline
description: Answer a minor Project Mu repo question in a small read-only side chat that is not on the agent board — no session ritual, no edits, no commits. Use for "/offline <question>", or when JR says an answer-only question does not deserve a full session.
argument-hint: <question about the repo>
---

# /offline

Question: $ARGUMENTS

Answer-only. This session does not do the research itself and does not follow the session-start
ritual for this question: no label, no board listener, no NOTICE, no edits, no commits. The side
chat is the `repo-qa` subagent — read-only tools, not on the board. (It is Haiku under Claude Code;
OpenCode's copy inherits the session's model.)

1. If there is no question in `$ARGUMENTS`, ask JR for one and stop.
2. Spawn `repo-qa` with `subagent_type: "repo-qa"` and a self-contained prompt: the question,
   the repo root `/home/jrereyi/Project-Mu`, and a reminder to answer from `PROJECT_SPEC.md` and
   the code with `path:line` references. Record the agent ID from the spawn result.
   - Agent definitions load at session start, and each harness reads its own copy: Claude Code
     reads `.claude/agents/repo-qa.md`, OpenCode reads `.opencode/agents/repo-qa.md`. If the
     harness answers "Agent type 'repo-qa' not found", fall back to
     `subagent_type: "general-purpose"` with `model: "haiku"`, and put the rules from your
     harness's agent file at the top of the prompt (read-only, no shell, not a session, no
     board). Tell JR once that a restart gives the dedicated read-only agent.
3. When the answer arrives (the side chat may run in the background), relay it trimmed of filler.
   Do not add research of your own.
4. A follow-up question in the same session goes to the same side chat: `SendMessage` to that
   agent ID instead of spawning a new agent.
5. If the answer is thin or wrong, send one sharper follow-up. If it still fails, say so and
   offer to answer in this session instead.
6. If the question needs files changed, commands run, tests, a build, a plan or board traffic,
   stop and tell JR it is not an `/offline` job; let them decide what to run.
