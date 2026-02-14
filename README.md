# divera-ntfy-gateway

Pollt die DiVeRa-API auf neue Alarmierungen und sendet Push-Benachrichtigungen an einen **ntfy**-Topic.

## Features

- Polling von DiVeRa über:
  - `https://divera247.com/api/v2/alarms`
  - Fallback: `https://app.divera247.com/api/v2/pull/all`
- Versand an ntfy (`NTFY_URL` + `NTFY_TOPIC`)
- Optionaler ntfy Bearer-Token (`NTFY_AUTH_TOKEN`) für geschützte Topics
- Dedup + State-Datei unter `/var/lib/alarm-gateway/state.json`
- Optionales Shelly Plus Uni Input-Polling
- One-shot Check: `--check-divera-alarm`

## Installation

```bash
git clone https://github.com/dataklo/divera-ntfy-gateway.git
cd divera-ntfy-gateway
sudo bash scripts/install.sh
```

## Konfiguration

Datei:

```bash
/etc/alarm-gateway/alarm-gateway.env
```

Pflicht:

- `DIVERA_ACCESSKEY`
- `NTFY_URL`
- `NTFY_TOPIC`

Optional:

- `DIVERA_URL` (Default: `https://divera247.com/api/v2/alarms`)
- `DIVERA_FALLBACK_URL` (Default: `https://app.divera247.com/api/v2/pull/all`)
- `POLL_SECONDS` (Default: `20`)
- `STATE_FILE`
- `NTFY_PRIORITY`
- `NTFY_AUTH_TOKEN`
- `REQUEST_TIMEOUT`
- `VERIFY_TLS`
- `SHELLY_*` Variablen

Beispiel:

```env
DIVERA_ACCESSKEY="DEIN_DIVERA_KEY"
NTFY_URL="https://ntfy.sh"
NTFY_TOPIC="dein-zufaelliger-topic"
# Optional bei geschütztem Topic:
# NTFY_AUTH_TOKEN="<dein-ntfy-token>"
```

## Betrieb

```bash
sudo systemctl restart alarm-gateway
sudo systemctl status alarm-gateway --no-pager
sudo journalctl -u alarm-gateway -f
```

## Update

```bash
cd divera-ntfy-gateway
git pull
sudo bash scripts/update.sh
```

## Tests / Checks

Test-Push:

```bash
cd /opt/alarm-gateway
source venv/bin/activate
python3 alarm_gateway.py --test-push --test-title "Probealarm" --test-text "Testtext"
```

DiVeRa One-shot Check:

```bash
python3 alarm_gateway.py --check-divera-alarm --check-json
```

## Troubleshooting

Keine Pushs:

- Direkt ntfy testen:
  ```bash
  curl -d "test" https://DEIN-NTFY-SERVER/DEIN-TOPIC
  ```
- Bei 401/403: `NTFY_AUTH_TOKEN` setzen
- DiVeRa testen:
  ```bash
  curl "https://divera247.com/api/v2/alarms?accesskey=DEIN_KEY"
  curl "https://app.divera247.com/api/v2/pull/all?accesskey=DEIN_KEY"
  python3 alarm_gateway.py --check-divera-alarm --check-json
  ```
- Logs prüfen: `journalctl -u alarm-gateway -f`

## Lizenz

MIT
