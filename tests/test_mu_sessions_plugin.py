"""The Mu session-tab TUI plugin: new project sessions open, historical/other-project ones do not.

The plugin runs inside OpenCode's TUI, which has no pytest harness. This test evaluates its
source under Node with the plugin SDK replaced by a stub and a mocked TUI context, so the
auto-open and /mu-sessions logic are exercised without a terminal.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / ".opencode" / "plugins" / "mu-sessions" / "tui.js"


def test_mu_sessions_plugin_opens_only_new_project_sessions(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = tmp_path / "harness.mjs"
    script.write_text(r'''
import fs from "node:fs"
import assert from "node:assert/strict"

const source = fs.readFileSync(process.env.PLUGIN, "utf8")
  .replace('import { Plugin } from "@opencode/plugin/tui"', "const Plugin = { define: (value) => value }")
const module = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"))

const opened = []
const toasts = []
let layer
const now = Date.now()
const sessions = [
  { id: "ses_old", title: "Mu old (deepseek)", location: { directory: "/p" },
    time: { created: now - 100000, updated: now } },
  { id: "ses_new", title: "Mu new (deepseek)", location: { directory: "/p" },
    time: { created: now + 1000, updated: now + 1000 } },
  { id: "ses_other", title: "Mu other (deepseek)", location: { directory: "/other" },
    time: { created: now + 1000, updated: now + 1000 } },
  { id: "ses_chat", title: "Something else", location: { directory: "/p" },
    time: { created: now + 1000, updated: now + 1000 } },
]
let selected = "ses_new"
const context = {
  location: { directory: "/p" },
  data: { session: { list: () => sessions } },
  ui: {
    tabs: { enabled: () => true, open: (id) => opened.push(id),
            focus: (id) => opened.push("focus:" + id) },
    toast: { show: (value) => toasts.push(value) },
    dialog: { select: async () => selected },
    router: { navigate: () => { throw new Error("router must not be used when tabs are on") } },
  },
  keymap: { layer: (factory) => { layer = factory() } },
}

globalThis.setInterval = (fn) => { fn(); return 1 }
globalThis.clearInterval = () => {}

module.default.setup(context)
assert.deepEqual(opened, ["ses_new"])              // only a new, same-project Mu session
assert.equal(layer.commands[0].id, "mu.sessions.open")

await layer.commands[0].run()
assert.deepEqual(opened, ["ses_new", "focus:ses_new"])

selected = "ses_old"
await layer.commands[0].run()
assert.equal(opened.at(-1), "focus:ses_old")
assert.ok(toasts.some((toast) => String(toast.message).includes("Mu new")))
console.log("ok")
''', encoding="utf-8")
    env = {"PLUGIN": str(PLUGIN), "PATH": "/usr/bin:/bin:" + str(Path(node).parent)}
    run = subprocess.run([node, str(script)], capture_output=True, text=True, env=env, timeout=30)
    assert run.returncode == 0, run.stderr + run.stdout
    assert run.stdout.strip() == "ok"
