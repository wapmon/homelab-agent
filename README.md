# Homelab Agent

A custom Claude-powered agent for managing a Proxmox-based homelab
(Home Assistant, Pi-hole, Sonarr/Radarr/Transmission/Emby).

Runs on a dedicated LAN jumpbox. CLI interface. Autonomous after one
upfront authorization; stops to ask before destructive operations.

## Requirements

- Python 3.10+
- Node.js (the Claude CLI bundled with `claude-agent-sdk` requires it)
- A Claude Pro or Max subscription (authenticated via `claude auth login`)
- An SSH key pair; the public key must be in `authorized_keys` on each
  homelab host for the user defined in `config/inventory.yaml`
- A Proxmox API token (Datacenter → Permissions → API Tokens)
- A Home Assistant long-lived access token
- Pi-hole password (v6) or API token (v5)
- Sonarr / Radarr API keys (Settings → General)
- Emby API key (Settings → Advanced → API Keys)

## Setup

```bash
git clone <your repo> homelab-agent
cd homelab-agent
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Authenticate with your Claude subscription (one-time)
claude auth login

cp .env.example .env
# edit .env with real values

cp config/inventory.example.yaml config/inventory.yaml
# edit inventory to match your setup (this file is gitignored)
$EDITOR config/inventory.yaml
$EDITOR config/system_prompt.md   # optional; refine the agent's knowledge
```

Generate a dedicated SSH key for the agent:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/homelab_agent_ed25519 -C "homelab-agent"
# then copy the public key to each target host:
ssh-copy-id -i ~/.ssh/homelab_agent_ed25519.pub user@host
```

Set `SSH_KEY_PATH` in `.env` to point at the private key.

## Run

```bash
homelab-agent
```

You'll be asked to authorize the agent once. After that it can call any
of its tools autonomously. The system prompt instructs it to stop and
confirm before destructive operations (deleting media, removing
containers, network-reachability changes).

## Security posture

- **Dedicated SSH key** for the agent, separate from your personal key.
  Consider restricting it in `authorized_keys` with `command=`,
  `from=`, or `no-port-forwarding`.
- **Non-root SSH users** on each host where possible. Use a tight
  `sudoers` entry for the specific commands the agent needs.
- **Proxmox API token**, not the root password. Limit the token's
  privileges to what's actually needed (PVEVMAdmin on relevant guests
  is usually enough).
- **Secrets in `.env`**, never in `inventory.yaml` or the system
  prompt. `.env` is gitignored.
- **The agent itself runs as an unprivileged user** on the jumpbox.

## Extending

Add a new service by creating `homelab_agent/tools/<name>.py` exporting
a `build_tools(config)` function that returns a list of `@tool`-decorated
functions, then importing and adding it in `main.py:build_options`.

## Troubleshooting

- **"SSH error: Authentication failed"** — verify the public key is in
  `~/.ssh/authorized_keys` on the target host for the right user, and
  that `SSH_KEY_PATH` in `.env` points to the *private* key.
- **"Proxmox 401"** — token format must be `USER@REALM!TOKENID`, with
  the secret in `PROXMOX_TOKEN_SECRET`. The token also needs
  permissions assigned (Datacenter → Permissions).
- **"Pi-hole 401" on v6** — `PIHOLE_PASSWORD` is the web interface
  password. The agent handles the session/SID exchange.
- **HA returns empty entity list** — token may have expired; regenerate
  in Profile → Security.
