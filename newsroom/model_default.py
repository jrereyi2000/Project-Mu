"""The newsroom's article-writer model, in one place.

JR's decision (2026-11-12): every article — national slate, Mu Fantasy, custom commissions —
writes on `glm-5.3-flash` through OpenCode Go. That came out of the measured Week 18 comparison
(NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN, CLOSE_OUT_CHECKLIST P8-05): GLM-5.3-Flash placed first or tied
first in three of six slots, the incumbent `gemini-3.8-flash` never won one outright, and the Go
model cost ~$0.32 for six articles against ~$2.56.

Agent Wire posts, season memories and press conferences are **not** articles and stay on their own
models; see PROJECT_SPEC §7.3 and §7.12.
"""
import os

DEFAULT_WRITER_MODEL = os.environ.get("NEWSROOM_MODEL") or "glm-5.3-flash"
