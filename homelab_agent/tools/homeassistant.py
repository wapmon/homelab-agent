"""Home Assistant REST API tools."""
from __future__ import annotations

import httpx
from claude_agent_sdk import tool

from ..config import Config


def _client(config: Config) -> httpx.Client:
    return httpx.Client(
        base_url=config.secrets.hass_url,
        headers={
            "Authorization": f"Bearer {config.secrets.hass_token}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


def build_tools(config: Config) -> list:

    @tool(
        "hass_get_state",
        "Get the current state of a Home Assistant entity by entity_id "
        "(e.g. 'light.living_room', 'sensor.outdoor_temp'). Returns state "
        "and attributes.",
        {"entity_id": str},
    )
    async def hass_get_state(args: dict) -> dict:
        with _client(config) as c:
            r = c.get(f"/api/states/{args['entity_id']}")
        if r.status_code == 404:
            return {"content": [{"type": "text", "text": f"Entity {args['entity_id']} not found"}]}
        r.raise_for_status()
        data = r.json()
        attrs = "\n".join(f"  {k}: {v}" for k, v in data.get("attributes", {}).items())
        text = f"{data['entity_id']} = {data['state']}\nlast_changed: {data.get('last_changed')}\nattributes:\n{attrs}"
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "hass_search_entities",
        "Search for entities by a substring match on entity_id. Useful "
        "when you don't know the exact id, e.g. 'kitchen' returns "
        "everything with 'kitchen' in the id.",
        {"query": str},
    )
    async def hass_search_entities(args: dict) -> dict:
        with _client(config) as c:
            r = c.get("/api/states")
        r.raise_for_status()
        q = args["query"].lower()
        matches = [s for s in r.json() if q in s["entity_id"].lower()]
        if not matches:
            return {"content": [{"type": "text", "text": "(no matches)"}]}
        lines = [f"{s['entity_id']:<50} {s['state']}" for s in matches[:50]]
        suffix = f"\n... and {len(matches)-50} more" if len(matches) > 50 else ""
        return {"content": [{"type": "text", "text": "\n".join(lines) + suffix}]}

    @tool(
        "hass_call_service",
        "Call a Home Assistant service. domain is e.g. 'light', service is "
        "e.g. 'turn_on'. service_data is a JSON object passed as the body, "
        "typically including 'entity_id' and any service-specific fields. "
        "Pass service_data as a string of JSON.",
        {"domain": str, "service": str, "service_data_json": str},
    )
    async def hass_call_service(args: dict) -> dict:
        import json
        try:
            body = json.loads(args.get("service_data_json", "{}"))
        except json.JSONDecodeError as e:
            return {"content": [{"type": "text", "text": f"Invalid JSON: {e}"}]}
        with _client(config) as c:
            r = c.post(f"/api/services/{args['domain']}/{args['service']}", json=body)
        if r.status_code >= 400:
            return {"content": [{"type": "text", "text": f"HA error {r.status_code}: {r.text}"}]}
        changed = r.json()
        return {"content": [{"type": "text", "text": f"OK. {len(changed)} entities affected:\n" + "\n".join(s['entity_id'] + ' -> ' + s['state'] for s in changed)}]}

    @tool(
        "hass_get_logbook",
        "Get the HA logbook (recent events) for the last N hours, "
        "optionally filtered to one entity_id. Useful for diagnosing "
        "'why did that light turn on?' questions.",
        {"hours": int, "entity_id": str},
    )
    async def hass_get_logbook(args: dict) -> dict:
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=args["hours"])).isoformat()
        path = f"/api/logbook/{start}"
        params = {}
        if args.get("entity_id"):
            params["entity"] = args["entity_id"]
        with _client(config) as c:
            r = c.get(path, params=params)
        r.raise_for_status()
        entries = r.json()
        if not entries:
            return {"content": [{"type": "text", "text": "(no entries)"}]}
        lines = [f"{e.get('when','')} {e.get('name','')}: {e.get('message','')}" for e in entries[-80:]]
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return [hass_get_state, hass_search_entities, hass_call_service, hass_get_logbook]
