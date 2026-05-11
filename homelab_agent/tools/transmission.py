"""Transmission RPC tools. Handles the X-Transmission-Session-Id dance."""
from __future__ import annotations

from typing import Any, Optional

import httpx
from claude_agent_sdk import tool

from ..config import Config

_session_id: Optional[str] = None


def _rpc(config: Config, method: str, arguments: dict | None = None) -> dict:
    global _session_id
    s = config.secrets
    auth = (s.transmission_user, s.transmission_password) if s.transmission_user else None
    payload: dict[str, Any] = {"method": method}
    if arguments:
        payload["arguments"] = arguments
    headers = {}
    if _session_id:
        headers["X-Transmission-Session-Id"] = _session_id
    with httpx.Client(timeout=20.0) as c:
        r = c.post(s.transmission_url, json=payload, auth=auth, headers=headers)
        if r.status_code == 409:
            _session_id = r.headers.get("X-Transmission-Session-Id")
            headers["X-Transmission-Session-Id"] = _session_id or ""
            r = c.post(s.transmission_url, json=payload, auth=auth, headers=headers)
        r.raise_for_status()
        return r.json()


def build_tools(config: Config) -> list:

    @tool(
        "transmission_torrents",
        "List torrents with id, name, status, percent done, ratio, and speed.",
        {},
    )
    async def transmission_torrents(args: dict) -> dict:
        result = _rpc(config, "torrent-get", {
            "fields": ["id", "name", "status", "percentDone", "uploadRatio", "rateDownload", "rateUpload", "errorString"],
        })
        torrents = result.get("arguments", {}).get("torrents", [])
        status_names = {0: "stopped", 1: "check-wait", 2: "checking", 3: "dl-wait", 4: "downloading", 5: "seed-wait", 6: "seeding"}
        if not torrents:
            return {"content": [{"type": "text", "text": "(no torrents)"}]}
        lines = []
        for t in torrents:
            err = f" ERR:{t['errorString']}" if t.get("errorString") else ""
            lines.append(
                f"{t['id']:>4} {status_names.get(t['status'],'?'):<11} "
                f"{t['percentDone']*100:5.1f}% r={t['uploadRatio']:5.2f} "
                f"↓{t['rateDownload']//1024}KB/s ↑{t['rateUpload']//1024}KB/s  "
                f"{t['name'][:60]}{err}"
            )
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "transmission_action",
        "Action on torrents by id list: start, stop, verify, reannounce, or remove. "
        "If action is 'remove' and delete_data is true, files on disk are deleted too.",
        {"action": str, "ids": str, "delete_data": bool},
    )
    async def transmission_action(args: dict) -> dict:
        action = args["action"]
        try:
            ids = [int(x.strip()) for x in args["ids"].split(",") if x.strip()]
        except ValueError:
            return {"content": [{"type": "text", "text": "ids must be comma-separated integers"}]}
        method_map = {
            "start": "torrent-start",
            "stop": "torrent-stop",
            "verify": "torrent-verify",
            "reannounce": "torrent-reannounce",
            "remove": "torrent-remove",
        }
        if action not in method_map:
            return {"content": [{"type": "text", "text": f"unknown action; use one of {list(method_map)}"}]}
        arguments: dict[str, Any] = {"ids": ids}
        if action == "remove":
            arguments["delete-local-data"] = bool(args.get("delete_data", False))
        result = _rpc(config, method_map[action], arguments)
        return {"content": [{"type": "text", "text": f"{action} ids={ids}: {result.get('result')}"}]}

    return [transmission_torrents, transmission_action]
