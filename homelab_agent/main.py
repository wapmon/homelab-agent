"""Homelab Agent CLI.

Run: `homelab-agent` or `python -m homelab_agent.main`
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import mimetypes
import shlex
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import PromptSession
from rich.prompt import Confirm

from .config import load_config
from .tools import (
    arr,
    emby,
    homeassistant,
    inventory,
    pihole,
    proxmox,
    ssh,
    transmission,
    uptimekuma,
)

console = Console()

_SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _build_image_message(image_path: str, text: str) -> dict:
    """Validate and encode an image; return the ready-to-send message dict."""
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"No file at {path}")
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type not in _SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"Unsupported image type '{media_type}'. Use PNG, JPEG, GIF, or WEBP.")
    data = base64.standard_b64encode(path.read_bytes()).decode()
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
    ]
    if text.strip():
        content.append({"type": "text", "text": text.strip()})
    return {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


async def _once(msg: dict):
    yield msg


BANNER = """[bold cyan]Homelab Agent[/bold cyan] — Claude-powered ops for your Proxmox homelab.
Type your request. Use [bold]/exit[/bold] to quit, [bold]/reset[/bold] for a fresh conversation.
Attach a screenshot: [bold]/image /path/to/file.png [optional message][/bold]
"""


async def _spinner(msg: str = "thinking…") -> None:
    frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    try:
        while True:
            sys.stdout.write(f"\r{next(frames)} {msg}")
            sys.stdout.flush()
            await asyncio.sleep(0.1)
    finally:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def build_options(config) -> ClaudeAgentOptions:
    # Collect tools from every module
    all_tools = (
        inventory.build_tools(config)
        + ssh.build_tools(config)
        + proxmox.build_tools(config)
        + homeassistant.build_tools(config)
        + pihole.build_tools(config)
        + arr.build_tools(config)
        + transmission.build_tools(config)
        + emby.build_tools(config)
        + uptimekuma.build_tools(config)
    )

    server = create_sdk_mcp_server(
        name="homelab",
        version="0.1.0",
        tools=all_tools,
    )

    # Pre-approve every homelab tool so the agent runs autonomously after
    # the upfront confirmation. The system prompt instructs it to stop
    # and ask before truly destructive operations.
    tool_names = [f"mcp__homelab__{t.name}" for t in all_tools]

    return ClaudeAgentOptions(
        system_prompt=config.system_prompt,
        mcp_servers={"homelab": server},
        allowed_tools=tool_names,
        permission_mode="bypassPermissions",
        # Disable the built-in file/bash tools — the agent should reach the
        # homelab only through our typed tools, not local shell on the jumpbox.
        disallowed_tools=["Bash", "Edit", "Write", "Read"],
    )


def render_assistant(msg: AssistantMessage) -> None:
    for block in msg.content:
        if isinstance(block, TextBlock):
            if block.text.strip():
                console.print(Markdown(block.text))
        elif isinstance(block, ToolUseBlock):
            console.print(f"[dim]→ {block.name}({_short_args(block.input)})[/dim]")


def _short_args(d: dict, limit: int = 80) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in d.items())
    return s if len(s) <= limit else s[:limit] + "..."


async def repl() -> None:
    config = load_config()
    console.print(Panel(BANNER, border_style="cyan"))

    # Upfront authorization
    n_tools = sum(
        len(m.build_tools(config))
        for m in (inventory, ssh, proxmox, homeassistant, pihole, arr, transmission, emby, uptimekuma)
    )
    console.print(
        f"[yellow]The agent will be granted autonomous use of {n_tools} tools "
        "(SSH, Proxmox API, Home Assistant, Pi-hole, *arr, Transmission, Emby, Uptime Kuma).\n"
        "It will stop and ask before destructive operations per its system prompt.[/yellow]"
    )
    if not Confirm.ask("Authorize?", default=True):
        console.print("Exiting.")
        return

    options = build_options(config)

    session = PromptSession()
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = await session.prompt_async("\n> ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nExiting.")
                return

            stripped = user_input.strip()
            if stripped in ("/exit", "/quit"):
                return
            if stripped == "/reset":
                # New session: tear down and rebuild
                await client.disconnect()
                await client.connect()
                console.print("[dim](conversation reset)[/dim]")
                continue
            if not stripped:
                continue

            if stripped.startswith("/image "):
                rest = stripped[len("/image "):].strip()
                try:
                    # shlex.split handles quoted paths and backslash-escaped spaces
                    tokens = shlex.split(rest)
                except ValueError as exc:
                    console.print(f"[red]Image error: bad quoting — {exc}[/red]")
                    continue
                if not tokens:
                    console.print("[red]Usage: /image /path/to/file.png [message][/red]")
                    continue
                img_path = tokens[0]
                img_text = " ".join(tokens[1:])
                try:
                    msg = _build_image_message(img_path, img_text)
                except (FileNotFoundError, ValueError) as exc:
                    console.print(f"[red]Image error: {exc}[/red]")
                    continue
                query_arg = _once(msg)
            else:
                query_arg = stripped

            spinner_task = asyncio.create_task(_spinner())
            try:
                await client.query(query_arg)
                async for msg in client.receive_response():
                    if not spinner_task.done():
                        spinner_task.cancel()
                        await asyncio.gather(spinner_task, return_exceptions=True)
                    if isinstance(msg, AssistantMessage):
                        render_assistant(msg)
                    elif isinstance(msg, ResultMessage):
                        pass
            finally:
                if not spinner_task.done():
                    spinner_task.cancel()
                    await asyncio.gather(spinner_task, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(repl())
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
