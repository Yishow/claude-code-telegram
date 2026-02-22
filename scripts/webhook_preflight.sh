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

require_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    err "Missing .env file: $ENV_FILE"
    err "Create one first: cp .env.example .env"
    exit 1
  fi
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

main() {
  require_env_file

  local token username webhook_url webhook_path webhook_port webhook_secret
  token="$(strip_quotes "$(get_env TELEGRAM_BOT_TOKEN)")"
  username="$(strip_quotes "$(get_env TELEGRAM_BOT_USERNAME)")"
  webhook_url="$(strip_quotes "$(get_env WEBHOOK_URL)")"
  webhook_path="$(strip_quotes "$(get_env WEBHOOK_PATH)")"
  webhook_port="$(strip_quotes "$(get_env WEBHOOK_PORT)")"
  webhook_secret="$(strip_quotes "$(get_env TELEGRAM_WEBHOOK_SECRET_TOKEN)")"

  local ok=1

  info "Webhook preflight using: $ENV_FILE"

  if [ -z "$token" ] || [[ "$token" == "your_bot_token_here" ]]; then
    err "TELEGRAM_BOT_TOKEN is empty or placeholder."
    ok=0
  else
    info "TELEGRAM_BOT_TOKEN is set."
  fi

  if [ -z "$username" ] || [[ "$username" == "your_bot_username" ]]; then
    warn "TELEGRAM_BOT_USERNAME is empty or placeholder."
  else
    info "TELEGRAM_BOT_USERNAME is set: $username"
  fi

  if [ -z "$webhook_url" ]; then
    warn "WEBHOOK_URL is empty -> bot will use polling mode."
  else
    if [[ "$webhook_url" != https://* ]]; then
      err "WEBHOOK_URL must start with https://"
      ok=0
    fi
    info "WEBHOOK_URL: $webhook_url"
  fi

  if [ -z "$webhook_path" ]; then
    warn "WEBHOOK_PATH is empty; default is /webhook."
    webhook_path="/webhook"
  fi
  if [[ "$webhook_path" != /* ]]; then
    err "WEBHOOK_PATH must start with '/'"
    ok=0
  else
    info "WEBHOOK_PATH: $webhook_path"
  fi

  if [ -z "$webhook_port" ]; then
    warn "WEBHOOK_PORT is empty; default is 8443."
  else
    if ! [[ "$webhook_port" =~ ^[0-9]+$ ]]; then
      err "WEBHOOK_PORT must be an integer."
      ok=0
    else
      info "WEBHOOK_PORT: $webhook_port"
    fi
  fi

  if [ -n "$webhook_url" ] && [ -n "$webhook_path" ]; then
    case "$webhook_url" in
      *"$webhook_path") info "WEBHOOK_URL path matches WEBHOOK_PATH." ;;
      *) warn "WEBHOOK_URL does not end with WEBHOOK_PATH; verify reverse-proxy route." ;;
    esac
  fi

  if [ -n "$webhook_secret" ]; then
    info "TELEGRAM_WEBHOOK_SECRET_TOKEN is set."
  else
    warn "TELEGRAM_WEBHOOK_SECRET_TOKEN is empty (recommended to set for webhook mode)."
  fi

  echo
  info "Next actions:"
  echo "  1) Set WEBHOOK_URL=https://<public-domain>${webhook_path}"
  echo "  2) Configure reverse proxy to forward ${webhook_path} -> 127.0.0.1:${webhook_port:-8443}"
  echo "  3) Restart bot: make daemon-restart (or make run)"
  echo "  4) Verify mode is webhook and check Telegram webhook info:"
  echo "     curl \"https://api.telegram.org/bot<TOKEN>/getWebhookInfo\""

  if [ "$ok" -eq 0 ]; then
    exit 1
  fi
}

main "$@"
