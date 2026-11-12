# Visual mod check fixtures (second round, 2026-11-12)

Real saved frames and Windows OCR from the auditor's PC evidence, copied here so the
regressions in `tests/test_visual_mod_check_round2.py` run without the git-ignored
`data/` folders. Each `.json` keeps the frame's own `lines` and `words` (the OCR boxes matter
for the selection reader) and names its `.png` where pixels are needed. Nothing is
re-rendered, re-OCRed or edited; only the surrounding run metadata was dropped.

| Fixture | Source | Used for |
|---|---|---|
| `supersim_next_play_0003.json/.png` | `data/audit_reports/visual_mod_check/live_next_quarter/steps/0003_SUPERSIM_MENU` | F9: OCR truncates the selected `JUMP TO NEXT PLAY` to `JUMP TO NEXT`; the reader must name that row (via the catalog alias) and never `JUMP FORWARD`. |
| `exit_game_modal_0002.json/.png` | `data/audit_reports/visual_mod_check/live_quit_once/steps/0002_UNKNOWN` | F11: the measured `EXIT GAME` modal; the selected `NO` is absent from OCR and its filled row must not be read as `YES - EXIT TO MENU`. |
| `pause_quit_tooltip_0010.json/.png` | `data/audit_reports/visual_mod_check/live_quit_selected/steps/0010_QUIT_GAME_CONFIRM` | F11: the ordinary pause menu with the `QUIT GAME` tooltip ("Any unsaved progress will be lost."); it is `PAUSE_MENU`, not a confirmation. |
| `receiving_kickoff_0047.json` | `data/pilot_runs/audit-visual-supersim-0806e87/steps/0047_UNKNOWN` | F8: the receiving pre-snap kickoff (whole `KICKOFF` banner, scorebug `1st 11:00`, no kick controls). |
| `referee_0072.json` | same run, `0072_UNKNOWN` | F8 negative: referee close-up / empty OCR is not a kickoff state. |
| `playcall_0004.json`, `playcall_0022.json` | `data/audit_reports/visual_mod_check/live_quit_prepare/steps/0004_UNKNOWN`, `0022_UNKNOWN` | F10: the persistent offensive play-call frames a super-sim Back returns to; classified `GAME_PLAY_CALL`, not Pause. |
| `playcall_after_receiving.json` | `data/audit_reports/visual_mod_check/after_receiving_timeout` | F8/F10: the play call the receiving kickoff advanced to on its own. |

These are game screenshots (scorebugs, menus, player names) with no account, key or personal
data. `pytest tests/test_visual_mod_check_round2.py` is the consumer; the tests that need a
synthetic selection shape draw their own image instead of editing these.
