# Systemd User Service Setup (One-Click)

Run this project as a persistent Linux user service with one command.

## Quick Start

```bash
make daemon-up
```

This will:

1. Generate `~/.config/systemd/user/claude-telegram-bot.service`
2. Reload `systemd --user`
3. Enable auto-start on login
4. Restart service now
5. Show service status

The generated service runs:

```bash
uv run claude-telegram-bot
```

from your current project directory.

If `WEBHOOK_URL` is configured, daemon startup will force-kill any existing
listener on `WEBHOOK_PORT` before starting the bot.
You can disable this behavior with:

```bash
FORCE_KILL_WEBHOOK_PORT=false
```

## Common Commands

```bash
make daemon-status
make daemon-logs
make daemon-restart
make daemon-stop
make daemon-down
make daemon-uninstall
make daemon-print
```

## Keep Running After Logout

For some environments, user services stop when you log out. Enable lingering:

```bash
make daemon-linger
```

If it fails due to permissions, run:

```bash
sudo loginctl enable-linger $USER
```

## Troubleshooting

1. `systemd user session is unavailable`
Run in a normal login shell, then try:
```bash
loginctl enable-linger $USER
```

2. `uv not found`
Install dependencies first:
```bash
make dev
```

3. Service starts but bot fails
Check logs:
```bash
make daemon-logs
```
Verify `.env` exists and production settings are correct.

4. `Address already in use`
This usually means webhook mode is enabled (`WEBHOOK_URL` is set) and
`WEBHOOK_PORT` is already occupied.

Options:
```bash
# Option A: switch bot back to polling mode
# (remove WEBHOOK_URL from .env, then reinstall/restart daemon)

make daemon-install
make daemon-restart
```

```bash
# Option B: keep webhook mode but change WEBHOOK_PORT to a free port,
# then reinstall/restart daemon

make daemon-install
make daemon-restart
```

5. `Start request repeated too quickly`
The unit hit systemd start limits after repeated failures.

```bash
systemctl --user reset-failed claude-telegram-bot.service
make daemon-restart
```
