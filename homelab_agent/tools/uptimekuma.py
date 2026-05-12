"""Uptime Kuma tools via the Socket.IO API (uptime-kuma-api library)."""
from __future__ import annotations

import asyncio

from claude_agent_sdk import tool

from ..config import Config


def _with_api(config: Config, fn):
    """Connect, authenticate, run fn(api), disconnect. Returns fn's result."""
    from uptime_kuma_api import UptimeKumaApi

    if not config.secrets.uptime_kuma_url:
        raise RuntimeError("UPTIME_KUMA_URL is not set in .env")
    with UptimeKumaApi(config.secrets.uptime_kuma_url) as api:
        api.login(
            config.secrets.uptime_kuma_username,
            config.secrets.uptime_kuma_password,
        )
        return fn(api)


def build_tools(config: Config) -> list:

    @tool(
        "uptimekuma_list_monitors",
        "List all Uptime Kuma monitors with id, name, current status (UP/DOWN/PAUSED), "
        "and target URL or hostname.",
        {},
    )
    async def uptimekuma_list_monitors(args: dict) -> dict:
        monitors = await asyncio.to_thread(
            _with_api, config, lambda api: api.get_monitors()
        )
        if not monitors:
            return {"content": [{"type": "text", "text": "(no monitors)"}]}
        lines = []
        for m in monitors:
            hb = m.get("heartbeat") or {}
            if not m.get("active"):
                status = "PAUSED"
            elif hb.get("status") == 1:
                status = "UP"
            else:
                status = "DOWN"
            target = m.get("url") or m.get("hostname") or ""
            lines.append(f"[{m['id']}] {m['name']:<30} {status:<7} {target}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "uptimekuma_monitor_status",
        "Get detailed status, uptime %, ping, and last 10 heartbeats for one monitor by id.",
        {"id": int},
    )
    async def uptimekuma_monitor_status(args: dict) -> dict:
        mid = int(args["id"])

        def _fetch(api):
            monitors = api.get_monitors()
            monitor = next((m for m in monitors if m["id"] == mid), None)
            beats = api.get_monitor_beats(mid, 24) if monitor else []
            return monitor, beats

        monitor, beats = await asyncio.to_thread(_with_api, config, _fetch)
        if not monitor:
            return {"content": [{"type": "text", "text": f"Monitor {mid} not found"}]}
        hb = monitor.get("heartbeat") or {}
        up = monitor.get("uptime") or {}
        lines = [
            f"id={monitor['id']} name={monitor['name']}",
            f"type={monitor.get('type')}  target={monitor.get('url') or monitor.get('hostname', '')}",
            f"status={'UP' if hb.get('status') == 1 else 'DOWN'}  ping={hb.get('ping')}ms",
            f"uptime_24h={up.get('24', '?')}%  uptime_7d={up.get('720', '?')}%",
            f"interval={monitor.get('interval')}s  active={monitor.get('active')}",
        ]
        if beats:
            lines.append(f"\nLast {min(len(beats), 10)} heartbeats:")
            for b in list(beats)[-10:]:
                s = "UP" if b.get("status") == 1 else "DOWN"
                lines.append(
                    f"  {b.get('time')}  {s}  ping={b.get('ping')}ms  {b.get('msg', '')}"
                )
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    @tool(
        "uptimekuma_add_monitor",
        "Add a new monitor. type: http|tcp|ping|keyword|dns. "
        "url is required for http/keyword types. "
        "hostname + port are required for tcp. "
        "hostname is required for ping/dns. "
        "interval defaults to 60 seconds.",
        {"type": str, "name": str, "url": str, "hostname": str, "port": int, "interval": int, "keyword": str},
    )
    async def uptimekuma_add_monitor(args: dict) -> dict:
        from uptime_kuma_api import MonitorType

        type_map = {
            "http": MonitorType.HTTP,
            "tcp": MonitorType.PORT,
            "ping": MonitorType.PING,
            "keyword": MonitorType.KEYWORD,
            "dns": MonitorType.DNS,
        }
        monitor_type = type_map.get(args.get("type", "").lower())
        if not monitor_type:
            known = ", ".join(type_map)
            return {
                "content": [
                    {"type": "text", "text": f"Unknown type '{args.get('type')}'. Use: {known}"}
                ]
            }

        kwargs: dict = {
            "type": monitor_type,
            "name": args["name"],
            "interval": int(args.get("interval") or 60),
        }
        if args.get("url"):
            kwargs["url"] = args["url"]
        if args.get("hostname"):
            kwargs["hostname"] = args["hostname"]
        if args.get("port"):
            kwargs["port"] = int(args["port"])
        if args.get("keyword"):
            kwargs["keyword"] = args["keyword"]

        result = await asyncio.to_thread(
            _with_api, config, lambda api: api.add_monitor(**kwargs)
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Monitor added: id={result.get('monitorID')} msg={result.get('msg', '')}",
                }
            ]
        }

    @tool(
        "uptimekuma_pause_monitor",
        "Pause a monitor by id so it stops checking until resumed.",
        {"id": int},
    )
    async def uptimekuma_pause_monitor(args: dict) -> dict:
        result = await asyncio.to_thread(
            _with_api, config, lambda api: api.pause_monitor(int(args["id"]))
        )
        return {"content": [{"type": "text", "text": f"Paused monitor {args['id']}: {result}"}]}

    @tool(
        "uptimekuma_resume_monitor",
        "Resume a paused monitor by id.",
        {"id": int},
    )
    async def uptimekuma_resume_monitor(args: dict) -> dict:
        result = await asyncio.to_thread(
            _with_api, config, lambda api: api.resume_monitor(int(args["id"]))
        )
        return {"content": [{"type": "text", "text": f"Resumed monitor {args['id']}: {result}"}]}

    @tool(
        "uptimekuma_delete_monitor",
        "Permanently delete a monitor by id. Confirm with the operator before calling this.",
        {"id": int},
    )
    async def uptimekuma_delete_monitor(args: dict) -> dict:
        result = await asyncio.to_thread(
            _with_api, config, lambda api: api.delete_monitor(int(args["id"]))
        )
        return {"content": [{"type": "text", "text": f"Deleted monitor {args['id']}: {result}"}]}

    return [
        uptimekuma_list_monitors,
        uptimekuma_monitor_status,
        uptimekuma_add_monitor,
        uptimekuma_pause_monitor,
        uptimekuma_resume_monitor,
        uptimekuma_delete_monitor,
    ]
