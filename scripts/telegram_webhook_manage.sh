#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"

info() {
  printf '[INFO] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

err() {
  printf '[ERROR] %s\n' "$1" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    err "Missing command: $1"
    exit 1
  }
}

require_env_file() {
  [ -f "$ENV_FILE" ] || {
    err "Missing .env file: $ENV_FILE"
    exit 1
  }
}

get_env() {
  local key="$1"
  awk -F '=' -v k="$key" '
    BEGIN { IGNORECASE = 1 }
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      key=$1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (toupper(key) == toupper(k)) {
        value=substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
        exit
      }
    }
  ' "$ENV_FILE"
}

strip_quotes() {
  local v="$1"
  v="${v#\"}"
  v="${v%\"}"
  v="${v#\'}"
  v="${v%\'}"
  printf '%s' "$v"
}

load_config() {
  require_env_file
  TELEGRAM_BOT_TOKEN="$(strip_quotes "$(get_env TELEGRAM_BOT_TOKEN)")"
  WEBHOOK_URL="$(strip_quotes "$(get_env WEBHOOK_URL)")"
  WEBHOOK_SECRET="$(strip_quotes "$(get_env TELEGRAM_WEBHOOK_SECRET_TOKEN)")"

  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_BOT_TOKEN" = "your_bot_token_here" ]; then
    err "TELEGRAM_BOT_TOKEN is not configured in .env"
    exit 1
  fi

  API_BASE="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"
}

api_call() {
  local endpoint="$1"
  shift
  curl -sS "${API_BASE}/${endpoint}" "$@"
}

print_json_and_assert_ok() {
  python -c '
import json
import sys

raw = sys.stdin.read().strip() or "{}"
obj = json.loads(raw)
print(json.dumps(obj, ensure_ascii=False, indent=2))
if not obj.get("ok", False):
    sys.exit(1)
'
}

cmd_info() {
  load_config
  info "Querying Telegram webhook info"
  api_call getWebhookInfo | print_json_and_assert_ok
}

cmd_set() {
  load_config
  [ -n "$WEBHOOK_URL" ] || {
    err "WEBHOOK_URL is empty in .env"
    exit 1
  }
  [[ "$WEBHOOK_URL" == https://* ]] || {
    err "WEBHOOK_URL must start with https://"
    exit 1
  }

  info "Registering Telegram webhook"
  if [ -n "$WEBHOOK_SECRET" ]; then
    api_call setWebhook -X POST \
      --data-urlencode "url=${WEBHOOK_URL}" \
      --data-urlencode "drop_pending_updates=true" \
      --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
      | print_json_and_assert_ok
  else
    warn "TELEGRAM_WEBHOOK_SECRET_TOKEN is empty; proceeding without secret token"
    api_call setWebhook -X POST \
      --data-urlencode "url=${WEBHOOK_URL}" \
      --data-urlencode "drop_pending_updates=true" \
      | print_json_and_assert_ok
  fi

  info "Fetching webhook info after setWebhook"
  cmd_info
}

cmd_delete() {
  load_config
  info "Deleting Telegram webhook (switch back to polling)"
  api_call deleteWebhook -X POST \
    --data-urlencode "drop_pending_updates=true" \
    | print_json_and_assert_ok

  info "Fetching webhook info after deleteWebhook"
  cmd_info
}

usage() {
  cat <<EOF
Usage: scripts/telegram_webhook_manage.sh <command>

Commands:
  info    Show Telegram webhook status (getWebhookInfo)
  set     Register webhook from .env (setWebhook)
  delete  Delete webhook and fallback to polling (deleteWebhook)
EOF
}

main() {
  require_cmd curl
  require_cmd python

  case "${1:-info}" in
    info) cmd_info ;;
    set) cmd_set ;;
    delete) cmd_delete ;;
    -h|--help|help) usage ;;
    *) usage; err "Unknown command: $1"; exit 1 ;;
  esac
}

main "$@"
