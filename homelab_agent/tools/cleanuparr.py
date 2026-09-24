"""Cleanuparr API tools: job status, malware-blocker config, events, dry run."""
from __future__ import annotations

import httpx
from claude_agent_sdk import tool

from ..config import Config

_JOBS = ("MalwareBlocker", "QueueCleaner", "DownloadCleaner", "Seeker")


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def build_tools(config: Config) -> list:
    s = config.secrets
    if not (s.cleanuparr_url and s.cleanuparr_api_key):
        return []

    def _client() -> httpx.Client:
        return httpx.Client(
            base_url=s.cleanuparr_url.rstrip("/") + "/api",
            headers={"X-Api-Key": s.cleanuparr_api_key},
            timeout=30.0,
            verify=False,
        )

    @tool(
        "cleanuparr_status",
        "Cleanuparr overview: dry-run flag, job schedules and last/next runs, Malware "
        "Blocker settings (blocklists, deleteIfAnyFileBlocked), connected download clients "
        "and *arr instances. Check this before assuming Cleanuparr removed anything — in "
        "dry run it only logs.",
        {},
    )
    async def cleanuparr_status(args: dict) -> dict:
        try:
            with _client() as c:
                general = c.get("/configuration/general").json()
                jobs = c.get("/jobs").json()
                mb = c.get("/configuration/malware_blocker").json()
                clients = c.get("/configuration/download_client").json().get("clients", [])
                arrs = {a: c.get(f"/configuration/{a}").json().get("instances", []) for a in ("sonarr", "radarr")}
        except httpx.HTTPError as e:
            return _text(f"Cleanuparr error: {e}")
        lines = [f"dryRun={general.get('dryRun')}", "jobs:"]
        for j in jobs:
            lines.append(
                f"  {j['name']:<22} {j['status']:<14} schedule={j.get('schedule') or '-'} "
                f"last={j.get('previousRunTime') or '-'} next={j.get('nextRunTime') or '-'}"
            )
        lines.append(
            f"malware_blocker: enabled={mb['enabled']} cron={mb['cronExpression']} "
            f"deleteIfAnyFileBlocked={mb['deleteIfAnyFileBlocked']} ignorePrivate={mb['ignorePrivate']}"
        )
        for a in ("sonarr", "radarr"):
            b = mb.get(a) or {}
            lines.append(f"  {a}: enabled={b.get('enabled')} {b.get('blocklistType')} {b.get('blocklistPath')}")
        lines.append("download clients: " + ", ".join(f"{d['name']} ({d['host']}, enabled={d['enabled']})" for d in clients))
        for a, inst in arrs.items():
            lines.append(f"{a}: " + ", ".join(f"{i['name']} ({i['url']}, enabled={i['enabled']})" for i in inst))
        return _text("\n".join(lines))

    @tool(
        "cleanuparr_events",
        "Recent Cleanuparr events (removals, strikes, searches), newest first. 'search' filters "
        "by text (e.g. a show name); page_size defaults to 30. Dry-run events are marked.",
        {"search": str, "page_size": int},
    )
    async def cleanuparr_events(args: dict) -> dict:
        params: dict = {"page": 1, "pageSize": min(int(args.get("page_size") or 30), 200)}
        if args.get("search"):
            params["search"] = args["search"]
        try:
            with _client() as c:
                data = c.get("/events", params=params).json()
        except httpx.HTTPError as e:
            return _text(f"Cleanuparr error: {e}")
        items = data.get("items", [])
        if not items:
            return _text("(no events)")
        lines = [f"{data.get('totalCount')} total events"]
        for e in items:
            lines.append(
                f"{e['timestamp'][:19]} {e['severity']:<11} {e['eventType']:<22}"
                f"{' [DRY RUN]' if e.get('isDryRun') else ''} {e.get('itemTitle') or ''} — {e['message'][:160]}"
            )
        return _text("\n".join(lines))

    @tool(
        "cleanuparr_set_dry_run",
        "Turn Cleanuparr's global Dry Run on or off. Turning it OFF makes the Malware Blocker "
        "actually delete torrents and blocklist releases — confirm with the operator first.",
        {"enabled": bool},
    )
    async def cleanuparr_set_dry_run(args: dict) -> dict:
        try:
            with _client() as c:
                g = c.get("/configuration/general").json()
                g["dryRun"] = bool(args["enabled"])
                c.put("/configuration/general", json=g).raise_for_status()
                now = c.get("/configuration/general").json()["dryRun"]
        except httpx.HTTPError as e:
            return _text(f"Cleanuparr error: {e}")
        return _text(f"dryRun={now}")

    @tool(
        "cleanuparr_trigger_job",
        f"Run a Cleanuparr job once now. job is one of {', '.join(_JOBS)}. "
        "Results show up in cleanuparr_events and the container logs.",
        {"job": str},
    )
    async def cleanuparr_trigger_job(args: dict) -> dict:
        job = args["job"]
        if job not in _JOBS:
            return _text(f"Unknown job '{job}'. Choose one of: {', '.join(_JOBS)}")
        try:
            with _client() as c:
                r = c.post(f"/jobs/{job}/trigger")
                r.raise_for_status()
        except httpx.HTTPError as e:
            return _text(f"Cleanuparr error: {e}")
        return _text(r.json().get("message", f"{job} triggered"))

    return [cleanuparr_status, cleanuparr_events, cleanuparr_set_dry_run, cleanuparr_trigger_job]
