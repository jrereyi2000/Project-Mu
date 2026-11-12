// Mu agent-session tabs (JR, 2026-11-12).
//
// DeepSeek/Spark sessions are spawned headlessly in the shared OpenCode window: the API can
// create a session but cannot open a TUI tab (M-0743/M-0744). This TUI plugin closes that gap.
// It opens a tab for every *new* `Mu <label> (<agent>)` session after the plugin loads, and
// adds a `/mu-sessions` command that lists the Mu sessions updated in the last 12 hours and
// focuses the one JR picks. It never sends a prompt or a key to another session.
import { Plugin } from "@opencode/plugin/tui"

const TITLE_PREFIX = "Mu "
const POLL_MS = 2000
const RECENT_MS = 12 * 60 * 60 * 1000

function isMuSession(session) {
  return String(session?.title ?? "").startsWith(TITLE_PREFIX)
}

function updatedAt(session) {
  return Number(session?.time?.updated ?? session?.time?.created ?? 0)
}

function inThisProject(context, session) {
  const here = context.location?.directory
  const there = session?.location?.directory
  return !here || !there || here === there
}

export default Plugin.define({
  id: "mu.sessions",
  setup(context) {
    const opened = new Set()
    const since = Date.now()

    const tabsOn = () => {
      try {
        return context.ui.tabs.enabled()
      } catch {
        return false
      }
    }

    const openTab = (sessionID) => {
      try {
        if (tabsOn()) context.ui.tabs.open(sessionID)
      } catch {
        // A tab failure must never break the TUI; the picker still works.
      }
    }

    const announce = (title) => {
      try {
        context.ui.toast.show({
          title: "Mu session",
          message: title,
          variant: "info",
          duration: 4000,
        })
      } catch {
        // A toast is best effort.
      }
    }

    const watchNew = () => {
      let sessions = []
      try {
        sessions = context.data.session.list() ?? []
      } catch {
        return
      }
      for (const session of sessions) {
        if (!session?.id || !isMuSession(session) || !inThisProject(context, session)) continue
        if (opened.has(session.id)) continue
        // Only sessions created after the plugin loaded: historical sessions are not
        // auto-opened (the /mu-sessions picker lists them instead).
        if (Number(session.time?.created ?? 0) < since) continue
        opened.add(session.id)
        openTab(session.id)
        announce(String(session.title ?? session.id))
      }
    }

    const timer = setInterval(watchNew, POLL_MS)

    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "mu.sessions.open",
          title: "Open a Mu agent session",
          group: "Mu",
          palette: true,
          slash: { name: "mu-sessions", aliases: ["mu"] },
          run: async () => {
            let sessions = []
            try {
              sessions = context.data.session.list() ?? []
            } catch {
              return
            }
            const now = Date.now()
            const recent = sessions
              .filter((session) => session?.id && isMuSession(session)
                && inThisProject(context, session))
              .filter((session) => now - updatedAt(session) <= RECENT_MS)
              .sort((left, right) => updatedAt(right) - updatedAt(left))
            if (recent.length === 0) {
              context.ui.toast.show({
                message: "No Mu agent sessions in the last 12 hours",
                variant: "warning",
              })
              return
            }
            const choice = await context.ui.dialog.select({
              title: "Mu agent sessions",
              options: recent.map((session) => ({
                title: String(session.title ?? session.id),
                value: session.id,
                description: new Date(updatedAt(session)).toLocaleTimeString(),
              })),
            })
            if (!choice) return
            if (tabsOn()) context.ui.tabs.focus(choice)
            else context.ui.router.navigate({ type: "session", sessionID: choice })
          },
        },
      ],
    }))

    return () => clearInterval(timer)
  },
})
