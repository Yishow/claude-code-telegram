# Telegram Webhook Deployment Guide

Use this guide to switch the bot from polling to webhook mode.

## 1. Configure `.env`

```bash
WEBHOOK_URL=https://bot.example.com/webhook
WEBHOOK_PORT=8443
WEBHOOK_PATH=/webhook
TELEGRAM_WEBHOOK_SECRET_TOKEN=change-me-long-random-string
```

Notes:
- `WEBHOOK_URL` must be public HTTPS.
- `WEBHOOK_URL` path should match `WEBHOOK_PATH`.
- Keep `TELEGRAM_WEBHOOK_SECRET_TOKEN` private.

## 2. Run preflight checks

```bash
make webhook-check
```

This validates required env fields and prints next actions.

## 3. Reverse proxy

The app listens on `127.0.0.1:<WEBHOOK_PORT>` and your proxy terminates TLS.

### Nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location = /webhook {
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:8443/webhook;
    }
}
```

### Caddy example

```caddy
bot.example.com {
    reverse_proxy /webhook 127.0.0.1:8443
}
```

## 4. Restart bot

```bash
make daemon-restart
make daemon-status
```

Expected startup log:
- `Starting bot` with `mode=webhook`

## 5. Verify Telegram webhook state

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Check:
- `url` is your `WEBHOOK_URL`
- `last_error_date` / `last_error_message` are empty
- `pending_update_count` is not continuously growing

## 6. Rollback to polling

```bash
# In .env
WEBHOOK_URL=

# Restart
make daemon-restart
```

Expected startup log:
- `Starting bot` with `mode=polling`

## 7. Troubleshooting

- `wrong response from the webhook`: proxy path mismatch (`/webhook` mismatch)
- `SSL error`: certificate not valid for domain
- no updates received: firewall blocks 443 or DNS not pointing to your host
- repeated HTTP 401 in webhook route: `TELEGRAM_WEBHOOK_SECRET_TOKEN` mismatch
