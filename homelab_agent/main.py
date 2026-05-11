"""Homelab Agent CLI.

Run: `homelab-agent` or `python -m homelab_agent.main`
"""
from __future__ import annotations

import asyncio
import sys

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
from rich.prompt import Confirm, Prompt

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
)

console = Console()

BANNER = """[bold cyan]Homelab Agent[/bold cyan] — Claude-powered ops for your Proxmox homelab.
Type your request. Use [bold]/exit[/bold] to quit, [bold]/reset[/bold] for a fresh conversation.
"""


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
        for m in (inventory, ssh, proxmox, homeassistant, pihole, arr, transmission, emby)
    )
    console.print(
        f"[yellow]The agent will be granted autonomous use of {n_tools} tools "
        "(SSH, Proxmox API, Home Assistant, Pi-hole, *arr, Transmission, Emby).\n"
        "It will stop and ask before destructive operations per its system prompt.[/yellow]"
    )
    if not Confirm.ask("Authorize?", default=True):
        console.print("Exiting.")
        return

    options = build_options(config)

    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = Prompt.ask("\n[bold green]>[/bold green]")
            except (EOFError, KeyboardInterrupt):
                console.print("\nExiting.")
                return

            if user_input.strip() in ("/exit", "/quit"):
                return
            if user_input.strip() == "/reset":
                # New session: tear down and rebuild
                await client.disconnect()
                await client.connect()
                console.print("[dim](conversation reset)[/dim]")
                continue
            if not user_input.strip():
                continue

            await client.query(user_input)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    render_assistant(msg)
                elif isinstance(msg, ResultMessage):
                    # End-of-turn marker; ignore unless you want stats
                    pass


def main() -> None:
    try:
        asyncio.run(repl())
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
