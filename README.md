# herdr-subs

A [herdr](https://herdr.dev) plugin that detects your AI coding harness
subscriptions and shows them in the sidebar — one glance tells you what
you're paying for and what's logged in.

```
subs
  main
  claude max · codex free · pi 3 providers · opencode go
```

## Install

```bash
herdr plugin install aman0singh/herdr-subs
```

## Sidebar setup (required)

herdr renders plugin data through custom `$token`s in your Space sidebar
rows. Add the token row to `~/.config/herdr/config.toml`:

```toml
[ui.sidebar.spaces]
rows = [
  ["state_icon", "workspace"],
  ["branch", "git_status"],
  ["$claude", "$codex", "$pi", "$opencode"],
]
```

Then `herdr server reload-config`. Tokens only render where they are
reported, so the row stays empty on every workspace except `subs`.
Style them however you like:

```toml
[{ token = "$claude", fg = "#ffb86c" }, { token = "$codex", fg = "#50fa7b" }]
```

## How it works

On server start (and whenever you run the refresh action) the plugin:

1. Runs its detectors (below) — each is best-effort and silently skips
   harnesses that are missing or logged out.
2. Finds or creates a workspace labeled `subs` (created with `--no-focus`,
   it never steals your screen).
3. Reports short tokens like `claude max` to that workspace via
   `herdr workspace report-metadata` with a 24h TTL, so stale data
   disappears on its own.

Refresh manually or from a keybinding:

```bash
herdr plugin action invoke subs.refresh
```

```toml
[[keys.command]]
key = "prefix+S"
type = "plugin_action"
command = "subs.refresh"
description = "refresh subscriptions"
```

Debug what the detectors see without touching herdr:

```bash
herdr plugin config-dir subs   # plugin files live alongside
python3 subs.py status         # raw detection JSON
```

## Supported harnesses

| Harness  | Detection source                          | Shows                          |
|----------|-------------------------------------------|--------------------------------|
| Claude   | `claude auth status`                      | plan (`max`/`pro`/`api-key`)   |
| Codex    | `~/.codex/auth.json` (ChatGPT JWT claims) | ChatGPT plan + renewal date    |
| Pi       | `~/.pi/agent/auth.json`                   | configured provider count      |
| OpenCode | `~/.local/share/opencode/auth.json`       | provider (e.g. `go`) or count  |

Everything is read locally; nothing leaves your machine.

## Adding a detector

Each detector is a small function returning
`{"value": "sidebar text", "detail": {...}}` or `None`:

```python
def detect_gemini():
    ...
    return {"value": "gemini advanced", "detail": {...}}

DETECTORS = {
    ...
    "gemini": detect_gemini,
}
```

Then add `"$gemini"` to your sidebar row. PRs welcome — see
[CONTRIBUTING](#adding-a-detector). Keep detectors read-only, fast
(<1s ideal), and exception-free.

## Uninstall

```bash
herdr plugin uninstall subs
```

The reported tokens expire within 24h thanks to the TTL; close the `subs`
workspace whenever you like.

## License

MIT — see [LICENSE](LICENSE).
