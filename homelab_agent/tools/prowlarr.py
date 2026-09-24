"""Prowlarr v1 API tools: indexers, app sync, and cross-indexer search."""
from __future__ import annotations

import re

import httpx
from claude_agent_sdk import tool

from ..config import Config

# File types that should never appear as a TV/movie release
_SUSPICIOUS = re.compile(r"\.(exe|lnk|scr|msi|bat|cmd|com|vbs|js|jar)\b", re.I)


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def build_tools(config: Config) -> list:
    s = config.secrets
    if not (s.prowlarr_url and s.prowlarr_api_key):
        return []

    def _client() -> httpx.Client:
        return httpx.Client(
            base_url=s.prowlarr_url.rstrip("/") + "/api/v1",
            headers={"X-Api-Key": s.prowlarr_api_key},
            timeout=90.0,  # searches through FlareSolverr can be slow
            verify=False,
        )

    @tool(
        "prowlarr_indexers",
        "List Prowlarr indexers with id, definition, enabled, priority (lower = preferred), "
        "and tags (the 'flaresolverr' tag routes an indexer through the FlareSolverr proxy). "
        "test=true also runs a live connectivity test on each (slower).",
        {"test": bool},
    )
    async def prowlarr_indexers(args: dict) -> dict:
        try:
            with _client() as c:
                tags = {t["id"]: t["label"] for t in c.get("/tag").json()}
                lines = []
                for i in c.get("/indexer").json():
                    line = (
                        f"id={i['id']} {i['name']:<20} def={i['definitionName']} "
                        f"enabled={i['enable']} priority={i['priority']} "
                        f"tags={[tags.get(t, t) for t in i['tags']]}"
                    )
                    if args.get("test"):
                        t = c.post("/indexer/test", json=i)
                        line += "  test=OK" if t.status_code < 300 else f"  test=FAIL {t.text[:150]}"
                    lines.append(line)
        except httpx.HTTPError as e:
            return _text(f"Prowlarr error: {e}")
        return _text("\n".join(lines) or "(no indexers)")

    @tool(
        "prowlarr_apps",
        "List the apps Prowlarr syncs indexers to (Sonarr/Radarr) with sync level and the "
        "URLs used in each direction. Use direct LAN IP:port here — http://<svc>.homelab:<port> "
        "does not work because *.homelab resolves to the Caddy proxy (80/443 only).",
        {},
    )
    async def prowlarr_apps(args: dict) -> dict:
        try:
            with _client() as c:
                apps = c.get("/applications").json()
        except httpx.HTTPError as e:
            return _text(f"Prowlarr error: {e}")
        lines = []
        for a in apps:
            f = {fl["name"]: fl.get("value") for fl in a["fields"]}
            lines.append(
                f"id={a['id']} {a['name']:<8} syncLevel={a['syncLevel']} "
                f"appUrl={f.get('baseUrl')} prowlarrUrl={f.get('prowlarrUrl')}"
            )
        return _text("\n".join(lines) or "(no apps)")

    @tool(
        "prowlarr_search",
        "Search through Prowlarr. indexer_ids is an optional comma-separated list (e.g. '1,8'); "
        "empty searches all enabled indexers. category 5000=TV, 2000=Movies, 0=all. "
        "Results flag executable file names (likely malware fakes).",
        {"query": str, "indexer_ids": str, "category": int},
    )
    async def prowlarr_search(args: dict) -> dict:
        params: list[tuple[str, str | int]] = [("query", args["query"]), ("type", "search"), ("limit", 100)]
        ids = [x.strip() for x in str(args.get("indexer_ids") or "").split(",") if x.strip()]
        params += [("indexerIds", i) for i in ids]
        if int(args.get("category") or 0):
            params.append(("categories", int(args["category"])))
        try:
            with _client() as c:
                results = c.get("/search", params=params).json()
        except httpx.HTTPError as e:
            return _text(f"Prowlarr search error: {e}")
        if not results:
            return _text("(no results)")
        results.sort(key=lambda x: x.get("seeders") or 0, reverse=True)
        lines = []
        for x in results[:40]:
            flag = "  !! EXECUTABLE" if _SUSPICIOUS.search(x.get("title", "")) else ""
            lines.append(
                f"[{x.get('indexer')}] {x.get('title','?')[:90]}  "
                f"{(x.get('size') or 0) / 1e9:.2f}GB  seeders={x.get('seeders')}{flag}"
            )
        return _text(f"{len(results)} results (top 40 by seeders):\n" + "\n".join(lines))

    @tool(
        "prowlarr_update_indexer",
        "Enable/disable an indexer or change its priority (1-50, lower = preferred). "
        "Pass enable and/or priority; omitted fields are unchanged. Syncs to apps automatically.",
        {"id": int, "enable": bool, "priority": int},
    )
    async def prowlarr_update_indexer(args: dict) -> dict:
        iid = int(args["id"])
        try:
            with _client() as c:
                i = c.get(f"/indexer/{iid}").json()
                if args.get("enable") is not None:
                    i["enable"] = bool(args["enable"])
                if args.get("priority"):
                    i["priority"] = int(args["priority"])
                r = c.put(f"/indexer/{iid}", json=i)
                r.raise_for_status()
        except httpx.HTTPError as e:
            return _text(f"Prowlarr error updating indexer {iid}: {e}")
        return _text(f"Indexer {iid} ({i['name']}): enabled={i['enable']} priority={i['priority']}")

    @tool(
        "prowlarr_delete_indexer",
        "Permanently delete a Prowlarr indexer by id. Confirm with the operator first. "
        "Then run prowlarr_sync_apps and check <app>_indexers: the synced copy in Sonarr/Radarr "
        "is not always removed, and may need deleting there too.",
        {"id": int},
    )
    async def prowlarr_delete_indexer(args: dict) -> dict:
        iid = int(args["id"])
        try:
            with _client() as c:
                name = c.get(f"/indexer/{iid}").json().get("name")
                c.delete(f"/indexer/{iid}").raise_for_status()
        except httpx.HTTPError as e:
            return _text(f"Prowlarr error deleting indexer {iid}: {e}")
        return _text(f"Deleted indexer {iid} ({name})")

    @tool(
        "prowlarr_sync_apps",
        "Push Prowlarr's indexers to Sonarr/Radarr now (ApplicationIndexerSync).",
        {},
    )
    async def prowlarr_sync_apps(args: dict) -> dict:
        try:
            with _client() as c:
                r = c.post("/command", json={"name": "ApplicationIndexerSync"})
                r.raise_for_status()
        except httpx.HTTPError as e:
            return _text(f"Prowlarr error: {e}")
        return _text(f"Sync queued (command id {r.json().get('id')})")

    return [
        prowlarr_indexers,
        prowlarr_apps,
        prowlarr_search,
        prowlarr_update_indexer,
        prowlarr_delete_indexer,
        prowlarr_sync_apps,
    ]
