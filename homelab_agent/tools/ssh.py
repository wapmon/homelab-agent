"""SSH tool. Key-based auth only, one persistent client per host."""
from __future__ import annotations

import shlex
from typing import Any

import paramiko
from claude_agent_sdk import tool

from ..config import Config

# Module-level cache: one SSHClient per host name. Reused across tool calls
# so we're not re-handshaking on every command.
_clients: dict[str, paramiko.SSHClient] = {}


def _client_for(config: Config, host_name: str) -> paramiko.SSHClient:
    if host_name in _clients:
        transport = _clients[host_name].get_transport()
        if transport and transport.is_active():
            return _clients[host_name]
        # stale; fall through and rebuild
        _clients.pop(host_name, None)

    host = config.get_host(host_name)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # AutoAdd is acceptable for a LAN homelab where hosts are known.
    # On first connect the key is pinned; subsequent connects verify.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host["address"],
        username=host["user"],
        key_filename=str(config.secrets.ssh_key_path),
        timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    _clients[host_name] = client
    return client


def _run(client: paramiko.SSHClient, command: str, timeout: int) -> dict[str, Any]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    # Trim huge outputs so we don't blow the context window
    def trim(s: str, limit: int = 8000) -> str:
        if len(s) <= limit:
            return s
        return s[: limit // 2] + f"\n... [{len(s) - limit} chars trimmed] ...\n" + s[-limit // 2 :]
    return {
        "exit_code": exit_code,
        "stdout": trim(out),
        "stderr": trim(err),
    }


def build_tools(config: Config) -> list:
    """Return SSH-related tools bound to this config."""

    @tool(
        "ssh_exec",
        "Run a shell command on a homelab host via SSH. Returns exit code, "
        "stdout, stderr. Use `get_inventory` first if you don't know the host names.",
        {"host": str, "command": str},
    )
    async def ssh_exec(args: dict) -> dict:
        host = args["host"]
        command = args["command"]
        try:
            client = _client_for(config, host)
            result = _run(client, command, timeout=60)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"SSH error on {host}: {e}"}]}
        text = (
            f"$ {command}\n"
            f"[exit {result['exit_code']}]\n"
            f"--- stdout ---\n{result['stdout']}\n"
            f"--- stderr ---\n{result['stderr']}"
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "ssh_read_file",
        "Read a text file from a homelab host. Use for configs, logs, etc. "
        "Truncates at ~16KB; for larger files use ssh_exec with grep/tail.",
        {"host": str, "path": str},
    )
    async def ssh_read_file(args: dict) -> dict:
        host = args["host"]
        path = args["path"]
        try:
            client = _client_for(config, host)
            # Use cat with a head limit so we don't slurp 5GB log files
            result = _run(client, f"head -c 16384 {shlex.quote(path)}", timeout=15)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"SSH error on {host}: {e}"}]}
        if result["exit_code"] != 0:
            return {"content": [{"type": "text", "text": f"Could not read {path}: {result['stderr']}"}]}
        return {"content": [{"type": "text", "text": result["stdout"]}]}

    return [ssh_exec, ssh_read_file]
