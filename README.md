# divera-ntfy-gateway

Pollt die DiVeRa-API auf neue Alarmierungen und sendet Push-Benachrichtigungen an einen **ntfy**-Topic.

## Features

- Polling von DiVeRa über den Alarm-Endpunkt mit Primary/Fallback-Domain (`www.divera247.com` -> `divera247.com`)
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

- `DIVERA_URL` (Default: `https://www.divera247.com/api/v2/alarms?accesskey=<API-Key>`)
- `DIVERA_FALLBACK_URL` (Default: `https://divera247.com/api/v2/alarms?accesskey=<API-Key>`)
- `POLL_SECONDS` (Default: `20`)
- `STATE_FILE`
- `NTFY_PRIORITY`
- `NTFY_PRIORITY_KEYWORDS` (z. B. `Probealarm=1,MANV=4`)
- `NTFY_AUTH_TOKEN`
- `REQUEST_TIMEOUT`
- `VERIFY_TLS`
- `SHELLY_*` Variablen
- `SHELLY_INPUT_EVENTS` (input-spezifische Titel/Texte)
- `SHELLY_OUTPUT_LEVELS` (Output-Schaltung nach Alarmlevel)
- `WEBHOOK_ENABLED`, `WEBHOOK_BIND`, `WEBHOOK_PORT`, `WEBHOOK_PATH`, `WEBHOOK_TOKEN`, `WEBHOOK_HEALTH_PATH`

URL-Format: `https://www.divera247.com/api/v2/alarms?accesskey=<API-Key>`

Beispiel:

```env
DIVERA_ACCESSKEY="DEIN_DIVERA_KEY"
NTFY_URL="https://ntfy.sh"
NTFY_TOPIC="dein-zufaelliger-topic"
# Optional bei geschütztem Topic:
# NTFY_AUTH_TOKEN="<dein-ntfy-token>"
# Optional: Priorität pro Stichwort im Titel überschreiben
# Format: STICHWORT=PRIO,STICHWORT=PRIO
# Es wird im TITEL gesucht (Stichwort muss im Titel enthalten sein).
# Wenn mehrere Stichwörter passen, wird die höchste Prio verwendet.
# Die eigentliche Meldung (Titel/Text) bleibt unverändert; es wird nur der Priority-Header gesetzt.
# Beispiel: Probealarm -> 1, MANV -> 4
# NTFY_PRIORITY_KEYWORDS="Probealarm=1,MANV=4"
# Case-insensitive Teilstring-Match: auch "MANV-Alles" trifft auf "MANV".
# Shelly Input-spezifische Meldungen:
# Format: INPUT_ID=TITEL|TEXT,INPUT_ID=TITEL|TEXT
# Beispiel: 0 und 1 lösen unterschiedliche Alarmtexte aus
# SHELLY_INPUT_EVENTS="0=Einsatzanforderung|Eingang 0 ausgelöst,1=MANV Meldung|Eingang 1 ausgelöst"

# Shelly Outputs nach Alarmlevel schalten:
# Format: OUTPUT_ID=LEVEL|LEVEL,OUTPUT_ID=LEVEL|LEVEL
# Beispiel: Output 0 bei Level 1/2, Output 1 bei Level 3/4/5
# SHELLY_OUTPUT_LEVELS="0=1|2,1=3|4|5"

# Optional: Webhook (Alarm von externem Rechner via curl auslösen)
# WEBHOOK_ENABLED="true"
# WEBHOOK_BIND="0.0.0.0"
# WEBHOOK_PORT="8080"
# WEBHOOK_PATH="/webhook/alarm"
# WEBHOOK_TOKEN="dein-geheimer-token"
# WEBHOOK_HEALTH_PATH="/healthz"
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

## Shelly Uni: Inputs + Outputs erweitern

## Webhook: Alarm von anderem Rechner auslösen

Wenn `WEBHOOK_ENABLED=true`, stellt der Service einen HTTP-Endpoint bereit.
Du kannst dann von einem anderen Rechner per `curl` einen Alarm senden.

Beispiel:

```bash
curl -X POST "http://<SERVER-IP>:8080/webhook/alarm" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title":"MANV extern","text":"Manueller Alarm","alarm_level":4}'
```

JSON-Felder:
- `title` (Pflicht)
- `text` (optional)
- `alarm_level` (optional, numerisch; steuert auch Shelly-Output-Level-Mapping)
- `address` (optional)

Health/Metrics (ohne Auth, nur Lesezugriff):

```bash
curl "http://<SERVER-IP>:8080/healthz"
```

Liefert u. a. einfache Laufzeitmetriken (`push_sent`, `webhook_requests`, `webhook_error`, ...).

Die ursprüngliche Meldung (Titel/Text) wird nicht verändert, sie wird so versendet wie übergeben.

- **Inputs:** Mit `SHELLY_INPUT_EVENTS` kann jeder Eingang eine eigene Alarmierung (Titel/Text) auslösen.
- **Outputs:** Mit `SHELLY_OUTPUT_LEVELS` lassen sich pro Output unterschiedliche Alarmlevel zuordnen.
- Bei mehreren aktiven Alarmen wird der **höchste erkannte Alarmlevel** verwendet.
- Wenn kein passender Alarmlevel aktiv ist, werden die konfigurierten Outputs ausgeschaltet.

## Stabilität & Sicherheit (Hardening)

- Thread-sichere State-Zugriffe per Lock für Polling + Webhook.
- Laufzeit-Konfigurationsprüfung beim Start (`validate_runtime_config`).
- Warnungen bei unsicheren Einstellungen (z. B. `VERIFY_TLS=false`, fehlendes `WEBHOOK_TOKEN`).
- systemd-Service enthält zusätzliche Hardening-/Ressourcenoptionen (`MemoryMax`, `CPUQuota`, `TasksMax`, ...).

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
# Liest automatisch /etc/alarm-gateway/alarm-gateway.env (falls vorhanden)
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
  curl -L "https://www.divera247.com/api/v2/alarms?accesskey=DEIN_KEY"
  curl -L "https://divera247.com/api/v2/alarms?accesskey=DEIN_KEY"
  python3 alarm_gateway.py --check-divera-alarm --check-json
  ```
- Wenn `DIVERA_ACCESSKEY` noch auf `PASTE_YOUR_DIVERA_ACCESSKEY_HERE` steht, wird nicht gepollt.
- Wenn `Missing push target` erscheint: `NTFY_URL` und `NTFY_TOPIC` setzen.
- Nach Änderungen an `/etc/alarm-gateway/alarm-gateway.env` immer neu starten: `sudo systemctl restart alarm-gateway`.
- CLI-Checks laden standardmäßig `/etc/alarm-gateway/alarm-gateway.env`; alternativ Pfad setzen mit `ALARM_GATEWAY_ENV_FILE=/pfad/zur.env`.
- Logs prüfen: `journalctl -u alarm-gateway -f`

## Lizenz

MIT
