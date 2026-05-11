"""Inventory introspection. Lets the agent self-discover the topology."""
from __future__ import annotations

import json

from claude_agent_sdk import tool

from ..config import Config


def build_tools(config: Config) -> list:

    @tool(
        "get_inventory",
        "Return the full homelab inventory: hosts (with SSH details and "
        "descriptions) and services (where they run, notes). Call this when "
        "you need to know what's available or look up a host name.",
        {},
    )
    async def get_inventory(args: dict) -> dict:
        # Strip nothing sensitive — inventory has no secrets in it.
        return {"content": [{"type": "text", "text": json.dumps(config.inventory, indent=2)}]}

    return [get_inventory]
