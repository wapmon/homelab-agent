"""Configuration loader.

Reads .env (via python-dotenv) and config/inventory.yaml. Exposes a single
`load_config()` function returning a Config dataclass that the rest of the
app consumes. No secret ever appears in agent prompts or log lines.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = PROJECT_ROOT / "config" / "inventory.yaml"
INVENTORY_EXAMPLE_PATH = PROJECT_ROOT / "config" / "inventory.example.yaml"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "config" / "system_prompt.md"


def _resolve_inventory_path() -> Path:
    if INVENTORY_PATH.exists():
        return INVENTORY_PATH
    if INVENTORY_EXAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"config/inventory.yaml not found. Copy the template:\n"
            f"  cp {INVENTORY_EXAMPLE_PATH.relative_to(PROJECT_ROOT)} "
            f"{INVENTORY_PATH.relative_to(PROJECT_ROOT)}\n"
            f"then edit it with your real hosts."
        )
    raise FileNotFoundError(f"No inventory file at {INVENTORY_PATH} or {INVENTORY_EXAMPLE_PATH}")


@dataclass
class Secrets:
    proxmox_host: str
    proxmox_user: str
    proxmox_token_id: str
    proxmox_token_secret: str
    proxmox_verify_ssl: bool
    hass_url: str
    hass_token: str
    pihole_url: str
    pihole_password: str
    sonarr_url: str
    sonarr_api_key: str
    radarr_url: str
    radarr_api_key: str
    transmission_url: str
    transmission_user: str
    transmission_password: str
    emby_url: str
    emby_api_key: str
    uptime_kuma_url: str
    uptime_kuma_username: str
    uptime_kuma_password: str
    ssh_key_path: Path


@dataclass
class Config:
    secrets: Secrets
    inventory: dict[str, Any]
    system_prompt: str
    hosts_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    services_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_host(self, name: str) -> dict[str, Any]:
        if name not in self.hosts_by_name:
            raise KeyError(
                f"Unknown host '{name}'. Known: {sorted(self.hosts_by_name)}"
            )
        return self.hosts_by_name[name]


def _env(key: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val or ""


def load_config() -> Config:
    load_dotenv(PROJECT_ROOT / ".env")

    secrets = Secrets(
        proxmox_host=_env("PROXMOX_HOST"),
        proxmox_user=_env("PROXMOX_USER", "root@pam"),
        proxmox_token_id=_env("PROXMOX_TOKEN_ID"),
        proxmox_token_secret=_env("PROXMOX_TOKEN_SECRET"),
        proxmox_verify_ssl=_env("PROXMOX_VERIFY_SSL", "false").lower() == "true",
        hass_url=_env("HASS_URL"),
        hass_token=_env("HASS_TOKEN"),
        pihole_url=_env("PIHOLE_URL"),
        pihole_password=_env("PIHOLE_PASSWORD"),
        sonarr_url=_env("SONARR_URL"),
        sonarr_api_key=_env("SONARR_API_KEY"),
        radarr_url=_env("RADARR_URL"),
        radarr_api_key=_env("RADARR_API_KEY"),
        transmission_url=_env("TRANSMISSION_URL"),
        transmission_user=_env("TRANSMISSION_USER"),
        transmission_password=_env("TRANSMISSION_PASSWORD"),
        emby_url=_env("EMBY_URL"),
        emby_api_key=_env("EMBY_API_KEY"),
        uptime_kuma_url=_env("UPTIME_KUMA_URL"),
        uptime_kuma_username=_env("UPTIME_KUMA_USERNAME", "admin"),
        uptime_kuma_password=_env("UPTIME_KUMA_PASSWORD"),
        ssh_key_path=Path(_env("SSH_KEY_PATH", "~/.ssh/id_ed25519")).expanduser(),
    )

    with _resolve_inventory_path().open() as f:
        inventory = yaml.safe_load(f)

    hosts_by_name = {h["name"]: h for h in inventory.get("hosts", [])}
    services_by_name = inventory.get("services", {})

    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    return Config(
        secrets=secrets,
        inventory=inventory,
        system_prompt=system_prompt,
        hosts_by_name=hosts_by_name,
        services_by_name=services_by_name,
    )
