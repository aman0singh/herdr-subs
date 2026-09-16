#!/usr/bin/env python3
"""subs — detect AI coding harness subscriptions and show them in the herdr sidebar.

Detects plan and live rate-limit usage for Claude Code, Codex, Pi, and
OpenCode, then reports short tokens (e.g. "claude max 5h:3% wk:7%") to a
dedicated "subs" workspace via `herdr workspace report-metadata`. Render
them in your Space sidebar rows as $claude, $codex, $pi, $opencode.

Commands:
  refresh   detect everything and update the sidebar (startup hook / action)
  status    print raw detection results as JSON (no herdr calls, for debugging)

Detectors are best-effort and never raise: a harness that is missing,
logged out, expired, or unreachable degrades to plan-only or is omitted.

Privacy: all data is read from local credential stores and each vendor's
own API. Nothing is sent anywhere else.
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
STATE_DIR = os.environ.get(
    "HERDR_PLUGIN_STATE_DIR",
    os.path.expanduser("~/.config/herdr/plugins/state/subs"),
)
STATE_PATH = os.path.join(STATE_DIR, "subs-state.json")
WORKSPACE_LABEL = "subs"
SOURCE = "subs"
TTL_MS = 86_400_000  # 24h: tokens self-clear if the plugin stops refreshing

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex CLI's public client id


def run(cmd, timeout=15):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    ).stdout


def http_json(url, headers=None, payload=None, timeout=10):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def herdr_json(*args):
    return json.loads(run([HERDR, *args]))["result"]


def jwt_payload(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def window_label(seconds):
    """Human label for a rate-limit window length."""
    if seconds <= 6 * 3600:
        return "5h"
    if seconds <= 2 * 86400:
        return "day"
    if seconds <= 8 * 86400:
        return "wk"
    return "mo"


# ---------------------------------------------------------------------------
# Detectors. Each returns {"value": sidebar text, "detail": {...}} or None.
# Register new ones in DETECTORS below.
# ---------------------------------------------------------------------------

def claude_access_token():
    if sys.platform == "darwin":
        out = run(["security", "find-generic-password", "-s",
                   "Claude Code-credentials", "-w"])
        return json.loads(out)["claudeAiOauth"]["accessToken"]
    path = os.path.expanduser("~/.claude/.credentials.json")
    return json.load(open(path))["claudeAiOauth"]["accessToken"]


def claude_usage():
    token = claude_access_token()
    return http_json(CLAUDE_USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.0",
    })


def detect_claude():
    out = run(["claude", "auth", "status"])
    d = json.loads(out)
    if not d.get("loggedIn"):
        return None
    if d.get("authMethod") == "apiKey":
        plan = "api-key"
    else:
        plan = d.get("subscriptionType") or "logged-in"

    parts = ["claude", plan]
    detail = {"plan": plan, "email": d.get("email")}
    try:
        u = claude_usage()
        five = u.get("five_hour") or {}
        week = u.get("seven_day") or {}
        if five.get("utilization") is not None:
            parts.append(f"5h:{five['utilization']:.0f}%")
            detail["five_hour"] = {
                "used_percent": five["utilization"],
                "resets_at": five.get("resets_at"),
            }
        if week.get("utilization") is not None:
            parts.append(f"wk:{week['utilization']:.0f}%")
            detail["seven_day"] = {
                "used_percent": week["utilization"],
                "resets_at": week.get("resets_at"),
            }
    except Exception:
        pass  # plan-only is fine
    return {"value": " ".join(parts), "detail": detail}


def codex_refresh(auth, path):
    """Rotate tokens exactly like the codex CLI does, persisting the result.

    OpenAI rotates refresh tokens on use, so the new tokens must be written
    back or the stored login breaks.
    """
    r = http_json(CODEX_REFRESH_URL, payload={
        "client_id": CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": auth["tokens"]["refresh_token"],
    })
    auth["tokens"]["id_token"] = r["id_token"]
    auth["tokens"]["access_token"] = r["access_token"]
    auth["tokens"]["refresh_token"] = r["refresh_token"]
    from datetime import datetime, timezone
    auth["last_refresh"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    json.dump(auth, open(path, "w"), indent=2)
    os.chmod(path, 0o600)


def detect_codex():
    path = os.path.expanduser("~/.codex/auth.json")
    if not os.path.exists(path):
        return None
    d = json.load(open(path))

    if d.get("auth_mode") == "chatgpt" and d.get("tokens", {}).get("id_token"):
        claims = jwt_payload(d["tokens"]["id_token"])
        authz = claims.get("https://api.openai.com/auth", {})
        plan = authz.get("chatgpt_plan_type") or "chatgpt"
        parts = ["codex", plan]
        detail = {
            "plan": plan,
            "email": claims.get("email"),
            "active_until": (authz.get("chatgpt_subscription_active_until") or "")[:10],
        }
        try:
            exp = jwt_payload(d["tokens"]["access_token"]).get("exp", 0)
            if exp < time.time() + 60:
                codex_refresh(d, path)
            u = http_json(CODEX_USAGE_URL, headers={
                "Authorization": f"Bearer {d['tokens']['access_token']}",
                "chatgpt-account-id": d["tokens"]["account_id"],
                "User-Agent": "codex/1.0",
            })
            rl = u.get("rate_limit") or {}
            for key in ("secondary_window", "primary_window"):
                w = rl.get(key)
                if w and w.get("used_percent") is not None:
                    label = window_label(w.get("limit_window_seconds") or 0)
                    parts.append(f"{label}:{w['used_percent']:.0f}%")
                    detail[label] = {
                        "used_percent": w["used_percent"],
                        "reset_at": w.get("reset_at"),
                    }
            if rl.get("limit_reached"):
                parts.append("(limit!)")
        except Exception:
            pass  # plan-only is fine
        return {"value": " ".join(parts), "detail": detail}

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
        "tokens": tokens,
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
