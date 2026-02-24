#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-claude-telegram-bot}"
SERVICE_FILE="${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="${SYSTEMD_USER_DIR}/${SERVICE_FILE}"
PORT_KILLER_SCRIPT="${PROJECT_DIR}/scripts/kill_port_listener.sh"

info() {
  printf '[INFO] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

die() {
  printf '[ERROR] %s\n' "$1" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

detect_uv_bin() {
  local uv_bin
  uv_bin="${UV_BIN:-}"
  if [ -z "$uv_bin" ]; then
    uv_bin="$(command -v uv || true)"
  fi
  [ -n "$uv_bin" ] || die "uv not found. Run: make dev"
  printf '%s' "$uv_bin"
}

load_env_value() {
  local key="$1"
  local env_file="${PROJECT_DIR}/.env"

  if [ -f "$env_file" ]; then
    # Parse simple KEY=VALUE entries (supports optional whitespace).
    sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)[[:space:]]*$/\1/p" "$env_file" \
      | tail -n 1 \
      | sed -E "s/^['\"]//; s/['\"]$//"
  fi
}

load_bool_value() {
  local key="$1"
  local default_value="$2"
  local raw_value

  raw_value="${!key:-}"
  if [ -z "$raw_value" ]; then
    raw_value="$(load_env_value "$key" || true)"
  fi
  if [ -z "$raw_value" ]; then
    raw_value="$default_value"
  fi

  case "${raw_value,,}" in
    1|true|yes|on) printf 'true' ;;
    0|false|no|off) printf 'false' ;;
    *) printf '%s' "$default_value" ;;
  esac
}

detect_webhook_url() {
  local webhook_url
  webhook_url="${WEBHOOK_URL:-}"
  if [ -z "$webhook_url" ]; then
    webhook_url="$(load_env_value WEBHOOK_URL || true)"
  fi
  printf '%s' "$webhook_url"
}

detect_webhook_port() {
  local webhook_port
  webhook_port="${WEBHOOK_PORT:-}"
  if [ -z "$webhook_port" ]; then
    webhook_port="$(load_env_value WEBHOOK_PORT || true)"
  fi
  if [ -z "$webhook_port" ]; then
    webhook_port="8443"
  fi
  printf '%s' "$webhook_port"
}

check_user_systemd() {
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    die "systemd user session is unavailable. If needed: loginctl enable-linger $USER"
  fi
}

build_unit_content() {
  local uv_bin path_env webhook_url webhook_port force_kill_port pre_start
  uv_bin="$(detect_uv_bin)"
  path_env="$(dirname "$uv_bin"):/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"
  webhook_url="$(detect_webhook_url)"
  webhook_port="$(detect_webhook_port)"
  force_kill_port="$(load_bool_value FORCE_KILL_WEBHOOK_PORT true)"
  pre_start=""
  if [ -n "$webhook_url" ] && [ "$force_kill_port" = "true" ]; then
    pre_start="ExecStartPre=${PORT_KILLER_SCRIPT} ${webhook_port}"
  fi

  cat <<EOF
[Unit]
Description=Claude Code Telegram Bot
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
${pre_start}
ExecStart=${uv_bin} run claude-telegram-bot
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PATH=${path_env}
Environment=PYTHONUNBUFFERED=1
Environment=UV_CACHE_DIR=${uv_cache_dir}

[Install]
WantedBy=default.target
EOF
}

install_unit() {
  require_cmd systemctl
  check_user_systemd
  mkdir -p "$SYSTEMD_USER_DIR"
  build_unit_content >"$UNIT_PATH"
  systemctl --user daemon-reload
  info "Installed user unit: $UNIT_PATH"
}

enable_unit() {
  check_user_systemd
  systemctl --user enable "$SERVICE_FILE" >/dev/null
  info "Enabled ${SERVICE_FILE}"
}

reset_failed_unit() {
  check_user_systemd
  systemctl --user reset-failed "$SERVICE_FILE" >/dev/null 2>&1 || true
}

start_unit() {
  reset_failed_unit
  systemctl --user start "$SERVICE_FILE"
  info "Started ${SERVICE_FILE}"
}

stop_unit() {
  check_user_systemd
  systemctl --user stop "$SERVICE_FILE" || true
  info "Stopped ${SERVICE_FILE}"
}

restart_unit() {
  reset_failed_unit
  systemctl --user restart "$SERVICE_FILE"
  info "Restarted ${SERVICE_FILE}"
}

status_unit() {
  check_user_systemd
  systemctl --user status "$SERVICE_NAME" --no-pager -l --lines=0 || true
  printf '\n'
  info "Recent logs:"
  journalctl --user -u "$SERVICE_NAME" -n "${STATUS_LOG_LINES:-20}" --output=short-iso --no-hostname \
    | format_journal_timestamp
}

format_journal_timestamp() {
  sed -u -E 's/^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2}:[0-9]{2})([.][0-9]+)?([+-][0-9]{2}:[0-9]{2})?[[:space:]]/\1 \2 /'
}

logs_unit() {
  check_user_systemd
  journalctl --user -u "$SERVICE_NAME" -f --output=short-iso --no-hostname \
    | format_journal_timestamp
}

disable_unit() {
  check_user_systemd
  systemctl --user disable "$SERVICE_FILE" >/dev/null || true
  info "Disabled ${SERVICE_FILE}"
}

uninstall_unit() {
  check_user_systemd
  disable_unit
  stop_unit
  rm -f "$UNIT_PATH"
  systemctl --user daemon-reload
  info "Removed user unit: $UNIT_PATH"
}

cmd_up() {
  install_unit
  enable_unit
  restart_unit
  status_unit
}

cmd_down() {
  stop_unit
  disable_unit
  status_unit
}

cmd_print() {
  if [ -f "$UNIT_PATH" ]; then
    cat "$UNIT_PATH"
  else
    warn "Unit not found: $UNIT_PATH"
    info "Showing generated unit content preview:"
    build_unit_content
  fi
}

cmd_linger() {
  require_cmd loginctl
  if loginctl enable-linger "$USER" >/dev/null 2>&1; then
    info "Enabled lingering for user: $USER"
  else
    warn "Could not enable lingering without elevated permissions."
    warn "Run manually: sudo loginctl enable-linger $USER"
    return 1
  fi
}

usage() {
  cat <<EOF
Usage: scripts/systemd_user_service.sh <command>

Commands:
  up         Install + enable + restart + status
  install    Install or update user unit file
  start      Start service
  stop       Stop service
  restart    Restart service
  status     Show service status
  logs       Tail service logs
  down       Stop + disable service
  uninstall  Disable + stop + remove user unit
  print      Print generated unit file
  linger     Try enabling login linger for this user
EOF
}

main() {
  local cmd="${1:-up}"
  case "$cmd" in
    up) cmd_up ;;
    install) install_unit ;;
    start) start_unit ;;
    stop) stop_unit ;;
    restart) restart_unit ;;
    status) status_unit ;;
    logs) logs_unit ;;
    down) cmd_down ;;
    uninstall) uninstall_unit ;;
    print) cmd_print ;;
    linger) cmd_linger ;;
    help|-h|--help) usage ;;
    *) usage; die "Unknown command: $cmd" ;;
  esac
}

main "$@"
