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
        verify=False,  # *.homelab certs come from Caddy's internal CA
    )


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


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
            return _text(text)
        f.__name__ = f"{name}_system_status"
        return f

    def _make_queue_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_queue",
            f"{name.capitalize()} download queue with each item's queue id, indexer, "
            "and status messages. Items with status 'warning' or 'error' typically need "
            "manual attention; the messages say why (e.g. failed import, bad path). "
            f"Use the id with {name}_queue_remove.",
            {},
        )
        async def f(args: dict) -> dict:
            with _client(url, api_key) as c:
                r = c.get("/api/v3/queue", params={"pageSize": 100, "includeUnknownSeriesItems": True})
            r.raise_for_status()
            records = r.json().get("records", [])
            if not records:
                return _text("(queue empty)")
            lines = []
            for q in records:
                title = q.get("title", "?")[:60]
                pct = 100.0 - (q.get("sizeleft", 0) / max(q.get("size", 1), 1) * 100)
                lines.append(
                    f"id={q.get('id')} [{q.get('status','?'):<10}] {pct:5.1f}%  {title}  "
                    f"-- {q.get('trackedDownloadStatus','')}/{q.get('trackedDownloadState','')} "
                    f"indexer={q.get('indexer') or '?'}"
                )
                for sm in q.get("statusMessages", []):
                    for m in sm.get("messages", []):
                        lines.append(f"      ! {m}")
                if q.get("errorMessage"):
                    lines.append(f"      ! {q['errorMessage']}")
            return _text("\n".join(lines))
        f.__name__ = f"{name}_queue"
        return f

    def _make_history_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_history",
            f"Recent {name} history events (grabs, imports, failures) with the indexer "
            "each grab came from. Useful for 'why didn't episode X download?' diagnosis "
            "and for spotting the same release being grabbed repeatedly.",
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
                + (f"  [{rec['data']['indexer']}]" if (rec.get("data") or {}).get("indexer") else "")
                for rec in records
            ]
            return _text("\n".join(lines) or "(empty)")
        f.__name__ = f"{name}_history"
        return f

    def _make_indexers_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_indexers",
            f"List {name.capitalize()}'s indexers with priority (lower = preferred), "
            "RSS/search enabled flags, and base URL. Indexers named '(Prowlarr)' are "
            "synced from Prowlarr; change those in Prowlarr, not here.",
            {},
        )
        async def f(args: dict) -> dict:
            try:
                with _client(url, api_key) as c:
                    r = c.get("/api/v3/indexer")
                r.raise_for_status()
            except httpx.HTTPError as e:
                return _text(f"{name} error: {e}")
            lines = []
            for i in r.json():
                base = next((fl.get("value") for fl in i.get("fields", []) if fl["name"] == "baseUrl"), "")
                lines.append(
                    f"id={i['id']} {i['name']:<30} priority={i.get('priority')} "
                    f"rss={i.get('enableRss')} search={i.get('enableAutomaticSearch')} {base}"
                )
            return _text("\n".join(lines) or "(no indexers)")
        f.__name__ = f"{name}_indexers"
        return f

    def _make_queue_remove_tool(name: str, url: str, api_key: str):
        @tool(
            f"{name}_queue_remove",
            f"Remove an item from the {name.capitalize()} queue by queue id (from {name}_queue). "
            "blocklist=true adds the release to the blocklist so it is never grabbed again "
            "(use for fakes/malware — deleting only in the download client lets it be re-grabbed). "
            "remove_from_client=true also deletes the torrent and its data from the download client. "
            "Blocklisting triggers a replacement search if auto-redownload is enabled.",
            {"id": int, "blocklist": bool, "remove_from_client": bool},
        )
        async def f(args: dict) -> dict:
            qid = int(args["id"])
            params = {
                "blocklist": str(bool(args.get("blocklist", False))).lower(),
                "removeFromClient": str(bool(args.get("remove_from_client", True))).lower(),
            }
            try:
                with _client(url, api_key) as c:
                    r = c.delete(f"/api/v3/queue/{qid}", params=params)
                r.raise_for_status()
            except httpx.HTTPError as e:
                return _text(f"{name} error removing queue item {qid}: {e}")
            return _text(f"Removed queue item {qid} ({params})")
        f.__name__ = f"{name}_queue_remove"
        return f

    tools = []
    for name in ("sonarr", "radarr"):
        url = getattr(config.secrets, f"{name}_url")
        api_key = getattr(config.secrets, f"{name}_api_key")
        if url and api_key:
            tools += [
                _make_status_tool(name, url, api_key),
                _make_queue_tool(name, url, api_key),
                _make_history_tool(name, url, api_key),
                _make_indexers_tool(name, url, api_key),
                _make_queue_remove_tool(name, url, api_key),
            ]
    return tools
