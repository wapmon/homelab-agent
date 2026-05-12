# homelab-agent

Claude-powered CLI agent for managing a Proxmox-based homelab. Runs interactively on a LAN jumpbox; the agent autonomously uses typed tools to query and control VMs, containers, and homelab services after upfront user authorization.

## Running

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e .
claude auth login          # authenticate with Claude subscription (no API key needed)
cp .env.example .env       # fill in real secrets
cp config/inventory.example.yaml config/inventory.yaml  # fill in real hosts/services

# Start
homelab-agent
```

REPL commands: `/exit` or `/quit` to stop, `/reset` for a fresh conversation.

## Architecture

```
homelab_agent/
├── config.py        # loads .env (Secrets dataclass) + inventory.yaml + system_prompt.md
├── main.py          # async REPL; build_options() assembles all tools into an MCP server
└── tools/           # one module per service domain
    ├── inventory.py     # get_inventory() — topology self-discovery
    ├── ssh.py           # ssh_exec, ssh_read_file (persistent client cache per host)
    ├── proxmox.py       # list/status/action/snapshot for VMs and LXCs
    ├── homeassistant.py # get_state, search_entities, call_service, get_logbook
    ├── pihole.py        # summary, recent_queries, set_blocking (Pi-hole v6 session auth)
    ├── arr.py           # system_status, queue, history for Sonarr + Radarr
    ├── transmission.py  # torrents list, torrent actions (session header caching)
    ├── emby.py          # active_sessions, library_scan
    └── uptimekuma.py    # list/status/add/pause/resume/delete monitors
```

`build_options()` in `main.py` calls `build_tools(config)` on every module, wraps all tools in an MCP server named `homelab`, pre-approves them, and disables the built-in Bash/Edit/Write/Read tools so the agent can only reach the homelab through typed interfaces.

## Adding a New Tool Module

1. Create `homelab_agent/tools/<service>.py`
2. Implement `build_tools(config) -> list` — return a list of `@tool`-decorated async functions
3. Import the module in `main.py` and add `<service>.build_tools(config)` to the chain in `build_options()`

Each tool function should return a plain string or dict; the agent reads whatever you return. Catch service-layer exceptions and return them as readable error strings rather than raising.

Optional services (e.g., Sonarr without Radarr) should check `config.secrets.<field>` before creating tools and return an empty list if unconfigured — the agent adapts automatically.

## Configuration

| File | Purpose | Committed? |
|------|---------|-----------|
| `.env` | All secrets (tokens, passwords, URLs) | No — gitignored |
| `config/inventory.yaml` | Homelab topology (hosts, services, locations) | No — gitignored |
| `config/system_prompt.md` | Agent domain instructions and operating rules | Yes |
| `.env.example` | Template listing every required variable | Yes |
| `config/inventory.example.yaml` | Template showing inventory schema | Yes |

`load_config()` in `config.py` returns a `Config` dataclass with `.secrets` (typed `Secrets` dataclass), `.inventory` (raw dict), `.system_prompt` (string), `.hosts_by_name`, and `.services_by_name`.

## Security Rules

- **Never commit `.env` or `config/inventory.yaml`** — pre-commit gitleaks hook guards this
- **Destructive operations** (delete, remove, network changes) must prompt the user before proceeding — enforced by the system prompt, not code
- The agent's SSH key (`SSH_KEY_PATH`) should be a dedicated ed25519 key, separate from personal keys; restrict it in `authorized_keys` with `command=`, `from=`, `no-port-forwarding`
- Proxmox access uses an API token (not root password) scoped to the minimum required role
- The agent runs on the jumpbox with no outbound internet access to homelab services — all connections are LAN-only
