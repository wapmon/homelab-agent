"""Pi-hole v6 API tools. v6 uses session auth: POST password -> SID -> use SID."""
from __future__ import annotations

import time
from typing import Optional

import httpx
from claude_agent_sdk import tool

from ..config import Config

_session_sid: Optional[str] = None
_session_expires: float = 0.0


def _authed_client(config: Config) -> httpx.Client:
    global _session_sid, _session_expires
    base = config.secrets.pihole_url.rstrip("/")
    if not _session_sid or time.time() > _session_expires - 30:
        with httpx.Client(base_url=base, timeout=10.0, verify=False) as c:
            r = c.post("/api/auth", json={"password": config.secrets.pihole_password})
            r.raise_for_status()
            data = r.json().get("session", {})
            _session_sid = data.get("sid")
            _session_expires = time.time() + int(data.get("validity", 1800))
    return httpx.Client(
        base_url=base,
        headers={"sid": _session_sid or ""},
        timeout=15.0,
        verify=False,
    )


def build_tools(config: Config) -> list:

    @tool(
        "pihole_summary",
        "Pi-hole summary stats: queries today, blocked today, percent blocked, "
        "clients seen, top domains.",
        {},
    )
    async def pihole_summary(args: dict) -> dict:
        with _authed_client(config) as c:
            stats = c.get("/api/stats/summary").json()
        q = stats.get("queries", {})
        text = (
            f"queries.total={q.get('total')} "
            f"blocked={q.get('blocked')} "
            f"percent_blocked={q.get('percent_blocked')}%\n"
            f"unique_domains={q.get('unique_domains')} "
            f"clients.active={stats.get('clients',{}).get('active')}"
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "pihole_recent_queries",
        "Last N DNS queries (default 50, max 500), with timestamp, client, "
        "domain, status (OK/BLOCKED), and reply type.",
        {"n": int},
    )
    async def pihole_recent_queries(args: dict) -> dict:
        n = min(int(args.get("n", 50)), 500)
        with _authed_client(config) as c:
            r = c.get("/api/queries", params={"length": n})
        r.raise_for_status()
        queries = r.json().get("queries", [])
        lines = [
            f"{q.get('time')} {q.get('client',{}).get('ip','?'):<15} "
            f"{q.get('domain','?'):<40} {q.get('status','?')}"
            for q in queries
        ]
        return {"content": [{"type": "text", "text": "\n".join(lines) or "(no queries)"}]}

    @tool(
        "pihole_set_blocking",
        "Enable or disable Pi-hole blocking. If duration_seconds > 0, "
        "blocking auto-resumes after that many seconds.",
        {"enabled": bool, "duration_seconds": int},
    )
    async def pihole_set_blocking(args: dict) -> dict:
        body = {"blocking": args["enabled"]}
        if args.get("duration_seconds", 0) > 0:
            body["timer"] = args["duration_seconds"]
        with _authed_client(config) as c:
            r = c.post("/api/dns/blocking", json=body)
        r.raise_for_status()
        return {"content": [{"type": "text", "text": f"blocking={r.json()}"}]}

    return [pihole_summary, pihole_recent_queries, pihole_set_blocking]
