# divera-ntfy-gateway

DiVeRa Polling + Webhook Trigger + HA-Failover für ntfy.

## Was jetzt zusätzlich drin ist (alle 5 Punkte)

1. **Cluster-Schutz zwischen Standorten** über optionales `CLUSTER_SHARED_TOKEN` (Peer-Health nur mit Token).  
2. **Replay-Schutz für Webhook-Trigger** via `ts` + `sig` (HMAC-SHA256), optional aktivierbar.  
3. **Retry + Queue bei ntfy-Ausfall** (wird gepuffert und später nachgesendet).  
4. **Prometheus-Metriken** über separaten Metrics-Endpunkt (`HEALTH_METRICS_PATH`).  
5. **Audit-Log** als JSON-Lines (`AUDIT_LOG_FILE`).

## Kernkonfiguration

- `NODE_ID` eindeutiger Name
- `NODE_PRIORITY` 1–100 (100 = höchste Priorität)
- `PEER_NODES` unterstützt **IP oder Domain inkl. Port**, z. B.:
  - `10.8.0.12:8081`
  - `ntfy.dataklo.de:8084`
- `WEBHOOK_PORT` für Trigger/API/UI
- `HEALTH_PORT` separat für Health/Metrics (sollte ≠ `WEBHOOK_PORT`)

## NTFY Fallback Server (Antwort auf deine Frage)

**Ja, das ist absolut sinnvoll** und jetzt direkt unterstützt:

- Primär: `NTFY_URL`
- Fallbacks: `NTFY_FALLBACK_URLS` (kommagetrennt)
- Retries: `NTFY_RETRY_ATTEMPTS`, `NTFY_RETRY_DELAY_SECONDS`

Wenn der primäre ntfy-Server nicht erreichbar ist, wird automatisch auf Fallback-Ziele gewechselt.

Beispiel:

```env
NTFY_URL="https://ntfy-primary.example.de"
NTFY_FALLBACK_URLS="https://ntfy-backup1.example.de,https://ntfy-backup2.example.de"
NTFY_RETRY_ATTEMPTS="3"
NTFY_RETRY_DELAY_SECONDS="1.0"
```

## Case-insensitive Keyword Matching

`NTFY_PRIORITY_KEYWORDS` ist vollständig case-insensitive (`casefold()`), also z. B. `MANV`, `manv`, `ManV`, `mAnV` sind identisch.

Beispiel:

```env
NTFY_PRIORITY_KEYWORDS="Probealarm=1,MANV=4"
```

## HA-Verhalten

- Der Node mit der höchsten `NODE_PRIORITY` sendet DiVeRa-Alarme.
- Andere Nodes bleiben Standby (kein doppeltes Senden).
- Bei gleicher Priorität entscheidet `NODE_ID` als Tie-Breaker.

## Endpunkte

Bei `WEBHOOK_PORT=8080`, `HEALTH_PORT=8081`:

- POST JSON: `http://<HOST>:8080/webhook/alarm`
- GET Trigger: `http://<HOST>:8080/webhook/trigger?...`
- UI: `http://<HOST>:8080/`
- Health: `http://<HOST>:8081/healthz`
- Prometheus: `http://<HOST>:8081/metrics`

## Replay-Schutz (optional)

Aktivieren:

```env
WEBHOOK_REPLAY_PROTECTION="true"
WEBHOOK_HMAC_SECRET="<secret>"
WEBHOOK_MAX_SKEW_SECONDS="120"
```

Dann muss der Trigger `ts` und `sig` enthalten (oder Header `X-Webhook-Timestamp`, `X-Webhook-Signature`).

## Beispiel cURL

```bash
curl -X POST "http://<HOST>:8080/webhook/alarm" \
  -H "Content-Type: application/json" \
  -d '{"title":"MANV extern","text":"Alarm von Standort B","address":"Musterstr. 1","priority":4}'
```

```bash
curl "http://<HOST>:8080/webhook/trigger?title=Einsatz%20extern&text=URL%20Trigger&address=Hauptstrasse%201&priority=4"
```

```bash
curl "http://<HOST>:8081/healthz"
curl "http://<HOST>:8081/metrics"
```
