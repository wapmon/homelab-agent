"""Sonarr/Radarr v3 API tools."""
from __future__ import annotations

import httpx
from claude_agent_sdk import tool

from ..config import Config


def _client(url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=url.rstrip("/"),
        headers={"X-Api-Key": api_key},
        timeout=20.0,
    )


def build_tools(config: Config) -> list:

    def _make_status_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_system_status",
            f"{name.capitalize()} system status: version, branch, app data path, "
            "and whether the indexer/download client are reachable.",
            {},
        )
        async def f(args: dict) -> dict:
            with _client(url, api_key) as c:
                status = c.get("/api/v3/system/status").json()
                health = c.get("/api/v3/health").json()
            text = (
                f"version={status.get('version')} branch={status.get('branch')}\n"
                f"appData={status.get('appData')}\n"
                f"health issues: {len(health)}\n"
                + "\n".join(f"  [{h.get('type')}] {h.get('source')}: {h.get('message')}" for h in health)
            )
            return {"content": [{"type": "text", "text": text}]}
        f.__name__ = f"{name}_system_status"
        return f

    def _make_queue_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_queue",
            f"{name.capitalize()} download queue. Shows items being downloaded, "
            "imported, or stuck. Items with status 'warning' or 'error' "
            "typically need manual attention.",
            {},
        )
        async def f(args: dict) -> dict:
            with _client(url, api_key) as c:
                r = c.get("/api/v3/queue", params={"pageSize": 100, "includeUnknownSeriesItems": True})
            r.raise_for_status()
            records = r.json().get("records", [])
            if not records:
                return {"content": [{"type": "text", "text": "(queue empty)"}]}
            lines = []
            for q in records:
                title = q.get("title", "?")[:60]
                pct = 100.0 - (q.get("sizeleft", 0) / max(q.get("size", 1), 1) * 100)
                lines.append(f"[{q.get('status','?'):<10}] {pct:5.1f}%  {title}  -- {q.get('trackedDownloadState','')}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        f.__name__ = f"{name}_queue"
        return f

    def _make_history_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_history",
            f"Recent {name} history events (grabs, imports, failures). "
            "Useful for 'why didn't episode X download?' diagnosis.",
            {"page_size": int},
        )
        async def f(args: dict) -> dict:
            ps = min(int(args.get("page_size", 30)), 100)
            with _client(url, api_key) as c:
                r = c.get("/api/v3/history", params={"pageSize": ps, "sortKey": "date", "sortDirection": "descending"})
            r.raise_for_status()
            records = r.json().get("records", [])
            lines = [
                f"{rec.get('date','')}  {rec.get('eventType','?'):<20}  {rec.get('sourceTitle','?')[:80]}"
                for rec in records
            ]
            return {"content": [{"type": "text", "text": "\n".join(lines) or "(empty)"}]}
        f.__name__ = f"{name}_history"
        return f

    tools = []
    if config.secrets.sonarr_url and config.secrets.sonarr_api_key:
        tools += [
            _make_status_tool("sonarr", config.secrets.sonarr_url, config.secrets.sonarr_api_key),
            _make_queue_tool("sonarr", config.secrets.sonarr_url, config.secrets.sonarr_api_key),
            _make_history_tool("sonarr", config.secrets.sonarr_url, config.secrets.sonarr_api_key),
        ]
    if config.secrets.radarr_url and config.secrets.radarr_api_key:
        tools += [
            _make_status_tool("radarr", config.secrets.radarr_url, config.secrets.radarr_api_key),
            _make_queue_tool("radarr", config.secrets.radarr_url, config.secrets.radarr_api_key),
            _make_history_tool("radarr", config.secrets.radarr_url, config.secrets.radarr_api_key),
        ]
    return tools
