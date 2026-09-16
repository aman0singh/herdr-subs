#!/usr/bin/env python3
"""subs — detect AI coding harness subscriptions and show them in the herdr sidebar.

Detects subscription/plan info for Claude Code, Codex, Pi, and OpenCode,
then reports short tokens (e.g. "claude max", "codex free") to a dedicated
"subs" workspace via `herdr workspace report-metadata`. Users render the
tokens in their Space sidebar rows as $claude, $codex, $pi, $opencode.

Commands:
  refresh   detect everything and update the sidebar (startup hook / action)
  status    print raw detection results as JSON (no herdr calls, for debugging)

Detectors are best-effort and never raise: a harness that is missing,
logged out, or unreadable is simply omitted.
"""

import base64
import json
import os
import subprocess
import sys

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
STATE_DIR = os.environ.get(
    "HERDR_PLUGIN_STATE_DIR",
    os.path.expanduser("~/.config/herdr/plugins/state/subs"),
)
STATE_PATH = os.path.join(STATE_DIR, "subs-state.json")
WORKSPACE_LABEL = "subs"
SOURCE = "subs"
TTL_MS = 86_400_000  # 24h: tokens self-clear if the plugin stops refreshing


def run(cmd, timeout=15):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    ).stdout


def herdr_json(*args):
    return json.loads(run([HERDR, *args]))["result"]


def jwt_payload(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ---------------------------------------------------------------------------
# Detectors. Each returns {"value": sidebar text, "detail": {...}} or None.
# Add your own and register it in DETECTORS below.
# ---------------------------------------------------------------------------

def detect_claude():
    out = run(["claude", "auth", "status"])
    d = json.loads(out)
    if not d.get("loggedIn"):
        return None
    if d.get("authMethod") == "apiKey":
        plan = "api-key"
    else:
        plan = d.get("subscriptionType") or "logged-in"
    return {
        "value": f"claude {plan}",
        "detail": {"plan": plan, "email": d.get("email"), "org": d.get("orgName")},
    }


def detect_codex():
    path = os.path.expanduser("~/.codex/auth.json")
    if not os.path.exists(path):
        return None
    d = json.load(open(path))
    if d.get("auth_mode") == "chatgpt" and d.get("tokens", {}).get("id_token"):
        claims = jwt_payload(d["tokens"]["id_token"])
        auth = claims.get("https://api.openai.com/auth", {})
        plan = auth.get("chatgpt_plan_type") or "chatgpt"
        return {
            "value": f"codex {plan}",
            "detail": {
                "plan": plan,
                "email": claims.get("email"),
                "active_until": (auth.get("chatgpt_subscription_active_until") or "")[:10],
            },
        }
    if d.get("OPENAI_API_KEY"):
        return {"value": "codex api-key", "detail": {"plan": "api-key"}}
    return None


def detect_pi():
    path = os.path.expanduser("~/.pi/agent/auth.json")
    if not os.path.exists(path):
        return None
    providers = sorted(json.load(open(path)).keys())
    if not providers:
        return None
    n = len(providers)
    return {
        "value": "pi 1 provider" if n == 1 else f"pi {n} providers",
        "detail": {"providers": providers},
    }


def detect_opencode():
    path = os.path.expanduser("~/.local/share/opencode/auth.json")
    if not os.path.exists(path):
        return None
    providers = sorted(json.load(open(path)).keys())
    if not providers:
        return None
    if len(providers) == 1:
        short = providers[0].replace("opencode-", "")
        value = f"opencode {short}"
    else:
        value = f"opencode {len(providers)} providers"
    return {"value": value, "detail": {"providers": providers}}


DETECTORS = {
    "claude": detect_claude,
    "codex": detect_codex,
    "pi": detect_pi,
    "opencode": detect_opencode,
}


# ---------------------------------------------------------------------------

def detect_all():
    results = {}
    for name, detector in DETECTORS.items():
        try:
            hit = detector()
        except Exception:
            hit = None
        if hit:
            results[name] = hit
    return results


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {"reported": []}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(state, open(STATE_PATH, "w"))


def ensure_workspace():
    """Reuse an existing workspace labeled 'subs', else create one unfocused."""
    for w in herdr_json("workspace", "list")["workspaces"]:
        if (w.get("label") or "").lower() == WORKSPACE_LABEL:
            return w["workspace_id"]
    res = herdr_json(
        "workspace", "create", "--label", WORKSPACE_LABEL, "--no-focus"
    )
    return res["workspace"]["workspace_id"]


def report(workspace_id, tokens):
    state = load_state()
    args = [
        HERDR, "workspace", "report-metadata", workspace_id,
        "--source", SOURCE, "--ttl-ms", str(TTL_MS),
    ]
    for name, value in tokens.items():
        args += ["--token", f"{name}={value}"]
    for stale in set(state["reported"]) - set(tokens):
        args += ["--clear-token", stale]
    run(args)
    state["reported"] = sorted(tokens)
    save_state(state)


def cmd_refresh():
    results = detect_all()
    tokens = {name: r["value"] for name, r in results.items()}
    workspace_id = ensure_workspace()
    report(workspace_id, tokens)
    print(json.dumps({
        "workspace_id": workspace_id,
        "subscriptions": {k: v["detail"] for k, v in results.items()},
    }, indent=2))


def cmd_status():
    print(json.dumps(detect_all(), indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if cmd == "refresh":
        cmd_refresh()
    elif cmd == "status":
        cmd_status()
    else:
        sys.exit(f"unknown command: {cmd}")
