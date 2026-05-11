"""Proxmox tools. Uses API token auth (no password)."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from claude_agent_sdk import tool
from proxmoxer import ProxmoxAPI

from ..config import Config


@lru_cache(maxsize=1)
def _api(host: str, user: str, token_id: str, token_secret: str, verify_ssl: bool) -> ProxmoxAPI:
    return ProxmoxAPI(
        host,
        user=user,
        token_name=token_id,
        token_value=token_secret,
        verify_ssl=verify_ssl,
    )


def _get_api(config: Config) -> ProxmoxAPI:
    s = config.secrets
    return _api(s.proxmox_host, s.proxmox_user, s.proxmox_token_id, s.proxmox_token_secret, s.proxmox_verify_ssl)


def build_tools(config: Config) -> list:
    node = config.inventory.get("proxmox", {}).get("node", "pve")

    @tool(
        "proxmox_list_guests",
        "List all VMs and LXC containers on the Proxmox node with their "
        "status, name, vmid, type, cpu, and memory usage.",
        {},
    )
    async def proxmox_list_guests(args: dict) -> dict:
        api = _get_api(config)
        vms = api.nodes(node).qemu.get()
        cts = api.nodes(node).lxc.get()
        rows = []
        for v in vms:
            rows.append(f"VM  {v['vmid']:>4}  {v['status']:<8}  {v.get('name','?'):<20}  cpu={v.get('cpu',0)*100:.1f}%  mem={v.get('mem',0)//1024//1024}MB")
        for c in cts:
            rows.append(f"CT  {c['vmid']:>4}  {c['status']:<8}  {c.get('name','?'):<20}  cpu={c.get('cpu',0)*100:.1f}%  mem={c.get('mem',0)//1024//1024}MB")
        return {"content": [{"type": "text", "text": "\n".join(rows) or "(no guests)"}]}

    @tool(
        "proxmox_guest_status",
        "Get detailed status for one VM or LXC by vmid. Returns uptime, "
        "resource usage, and config summary.",
        {"vmid": int, "guest_type": str},  # 'qemu' or 'lxc'
    )
    async def proxmox_guest_status(args: dict) -> dict:
        api = _get_api(config)
        vmid = args["vmid"]
        gtype = args["guest_type"]
        if gtype not in ("qemu", "lxc"):
            return {"content": [{"type": "text", "text": "guest_type must be 'qemu' or 'lxc'"}]}
        endpoint = getattr(api.nodes(node), gtype)(vmid)
        status = endpoint.status.current.get()
        config_data = endpoint.config.get()
        text = (
            f"vmid={vmid} type={gtype} name={status.get('name','?')}\n"
            f"status={status.get('status')} uptime={status.get('uptime',0)}s\n"
            f"cpus={config_data.get('cores','?')} memory={config_data.get('memory','?')}MB\n"
            f"cpu={status.get('cpu',0)*100:.1f}% mem={status.get('mem',0)//1024//1024}/{status.get('maxmem',0)//1024//1024}MB"
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "proxmox_action",
        "Start, stop, or reboot a VM/LXC. Action must be one of: "
        "start, stop, shutdown, reboot. Stop is a hard power-off; "
        "prefer shutdown for VMs and reboot for graceful cycles.",
        {"vmid": int, "guest_type": str, "action": str},
    )
    async def proxmox_action(args: dict) -> dict:
        api = _get_api(config)
        vmid, gtype, action = args["vmid"], args["guest_type"], args["action"]
        if gtype not in ("qemu", "lxc"):
            return {"content": [{"type": "text", "text": "guest_type must be 'qemu' or 'lxc'"}]}
        if action not in ("start", "stop", "shutdown", "reboot"):
            return {"content": [{"type": "text", "text": "action must be start|stop|shutdown|reboot"}]}
        endpoint = getattr(api.nodes(node), gtype)(vmid).status
        task = getattr(endpoint, action).post()
        return {"content": [{"type": "text", "text": f"{action} on {gtype}/{vmid} queued: {task}"}]}

    @tool(
        "proxmox_snapshot",
        "Create a snapshot of a VM/LXC. snapname must be alphanumeric, "
        "no spaces. Use before risky changes; safer than nothing.",
        {"vmid": int, "guest_type": str, "snapname": str, "description": str},
    )
    async def proxmox_snapshot(args: dict) -> dict:
        api = _get_api(config)
        vmid, gtype = args["vmid"], args["guest_type"]
        snapname = args["snapname"]
        description = args.get("description", "")
        if gtype not in ("qemu", "lxc"):
            return {"content": [{"type": "text", "text": "guest_type must be 'qemu' or 'lxc'"}]}
        endpoint = getattr(api.nodes(node), gtype)(vmid).snapshot
        task = endpoint.post(snapname=snapname, description=description)
        return {"content": [{"type": "text", "text": f"snapshot '{snapname}' on {gtype}/{vmid} queued: {task}"}]}

    return [proxmox_list_guests, proxmox_guest_status, proxmox_action, proxmox_snapshot]
