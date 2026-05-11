"""Emby API tools."""
from __future__ import annotations

import httpx
from claude_agent_sdk import tool

from ..config import Config


def _client(config: Config) -> httpx.Client:
    return httpx.Client(
        base_url=config.secrets.emby_url.rstrip("/"),
        headers={"X-Emby-Token": config.secrets.emby_api_key},
        timeout=20.0,
    )


def build_tools(config: Config) -> list:

    @tool(
        "emby_active_sessions",
        "Currently active Emby sessions: who is playing what, transcoding status, "
        "client device and player. Useful for 'why is my server slow?' answers.",
        {},
    )
    async def emby_active_sessions(args: dict) -> dict:
        with _client(config) as c:
            r = c.get("/Sessions")
        r.raise_for_status()
        sessions = r.json()
        active = [s for s in sessions if s.get("NowPlayingItem")]
        if not active:
            return {"content": [{"type": "text", "text": "(no active playback)"}]}
        lines = []
        for s in active:
            item = s["NowPlayingItem"]
            ts = s.get("TranscodingInfo")
            tcode = f" transcoding({ts.get('VideoCodec')}->{ts.get('Container')}, reason={ts.get('TranscodeReasons')})" if ts else " direct"
            lines.append(
                f"{s.get('UserName','?')} on {s.get('Client','?')}/{s.get('DeviceName','?')}: "
                f"{item.get('Name','?')} ({item.get('Type','?')}){tcode}"
            )
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "emby_library_scan",
        "Trigger a full library refresh (all libraries). Use after adding "
        "files manually.",
        {},
    )
    async def emby_library_scan(args: dict) -> dict:
        with _client(config) as c:
            r = c.post("/Library/Refresh")
        if r.status_code >= 300:
            return {"content": [{"type": "text", "text": f"Emby error {r.status_code}: {r.text}"}]}
        return {"content": [{"type": "text", "text": "Library refresh started"}]}

    return [emby_active_sessions, emby_library_scan]
