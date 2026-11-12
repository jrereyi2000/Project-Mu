---
name: context-check
description: Report how full this chat's context is, in tokens and as a percentage of the model's window, and whether it is over the hand-over threshold (60% by default). Builders run it every couple of board messages; at or over the threshold they hand over and stop. Use for "/context-check", "how full is my context", "check the context".
---

# /context-check

Run:

```bash
python3 -m tools.context_check
```

It reads this session's context — a Claude Code transcript found by `CLAUDE_CODE_SESSION_ID`, or
the OpenCode session named by `MU_OPENCODE_SESSION` — prints one line such as
`context: 58,203 / 1,000,000 tokens (5.8%) - under 60% (opencode ses_mu_…)`, and exits:

- **0** — under the threshold; carry on.
- **2** — at or over it; the last line says `OVER 60%: hand over now`.
- **1** — no usable source, or no usage yet; pass `--transcript PATH`, `--session ID` (a Claude
  session id or an OpenCode `ses_…`), or `--harness claude|opencode`.

Relay the line. Do not estimate context yourself, and do not treat a 0 as permission to skip the
check next time.

**Builders:** a 2 is a hard stop, not advice (JR, 2026-11-10). Finish nothing new; post the
hand-over NOTICE from `/builder` §4 (it carries this line), RELEASE your claims, stop the board
listener and end the turn. The next builder session starts from the hand-over.

Flags: `--limit N` (window size, default 1,000,000 for the DeepSeek v4.1 Flash routing),
`--threshold PCT`, `--harness auto|claude|opencode`, `--json`.
