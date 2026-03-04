# DiVeRa ↔ ntfy Gateway

Dieses Projekt verbindet **DiVeRa 24/7** mit **ntfy**:

- Das Gateway pollt Alarme aus DiVeRa.
- Neue Alarme werden als Push an ein ntfy-Topic gesendet.
- Optional kannst du Alarme zusätzlich per Webhook auslösen.

Die README ist so aufgebaut, dass du das Projekt auch ohne Vorwissen schnell betreiben kannst.

## Für wen ist das gedacht?

Für Feuerwehren, Hilfsorganisationen oder IT-Verantwortliche, die:

- DiVeRa-Alarme zentral abgreifen möchten,
- diese über ntfy verteilen wollen,
- optional ein hochverfügbares Setup mit mehreren Standorten betreiben.

---

## Features im Überblick

- **DiVeRa Polling** mit einstellbarem Intervall.
- **ntfy Versand** inkl. Token-Auth.
- **Fallback-ntfy-Server** und Retry-Logik bei Ausfällen.
- **Cluster-/HA-Modus**: nur der aktive Node sendet, die anderen bleiben Standby.
- **Webhook-Endpunkte** (POST + einfacher GET-Trigger) für externe Systeme.
- **Optionaler Replay-Schutz** (HMAC + Timestamp) für Webhook-Requests.
- **Health- und Prometheus-Metriken** auf separatem Port.
- **Audit-Logging** als JSON-Lines.
- **Keyword-basierte Priorität** (case-insensitive), z. B. `MANV=4`.

---

## Schnellstart (empfohlen mit systemd)

> Diese Anleitung ist für Einsteiger gedacht. Wenn du die Befehle 1:1 übernimmst, hast du in wenigen Minuten ein laufendes System.

### 1) Voraussetzungen

- Debian/Ubuntu-ähnliches Linux mit systemd
- Root-Rechte (oder ein Benutzer mit `sudo`)
- Netzwerkzugriff auf:
  - DiVeRa API
  - ntfy Server

### 2) Server vorbereiten

```bash
sudo apt update && sudo apt full-upgrade -y && sudo apt install nano htop git -y
```

### 3) Repository herunterladen

```bash
git clone https://github.com/dataklo/divera-ntfy-gateway.git
cd divera-ntfy-gateway
```

### 4) Installation starten

```bash
bash scripts/install.sh
```

Das Install-Script richtet den Dienst ein und erstellt die Konfigurationsdatei unter:
`/etc/alarm-gateway/alarm-gateway.env`

### 5) Direkt danach die ENV-Datei bearbeiten

> Wichtig: Nach `bash scripts/install.sh` musst du deine Zugangsdaten in der ENV-Datei eintragen.

```bash
sudo nano /etc/alarm-gateway/alarm-gateway.env
```

Mindestens diese Werte setzen:

```env
DIVERA_ACCESSKEY="<dein-divera-accesskey>"
NTFY_URL="https://ntfy.example.com"
NTFY_TOPIC="<dein-topic>"
```

### 6) Dienst starten und prüfen

```bash
sudo systemctl restart alarm-gateway
sudo systemctl status alarm-gateway
journalctl -u alarm-gateway -f
```

Wenn im Log keine Fehler erscheinen, läuft dein Gateway korrekt.

---

## Wichtige Konfigurationen

### Pflichtwerte

- `DIVERA_ACCESSKEY`: API-Key für DiVeRa
- `NTFY_URL`: Basis-URL deines ntfy Servers
- `NTFY_TOPIC`: Ziel-Topic für Push-Nachrichten

### ntfy Robustheit / Fallback

Wenn dein primärer ntfy-Server nicht erreichbar ist, können automatisch Fallback-Server verwendet werden.

```env
NTFY_URL="https://ntfy-primary.example.de"
NTFY_FALLBACK_URLS="https://ntfy-backup1.example.de,https://ntfy-backup2.example.de"
NTFY_RETRY_ATTEMPTS="3"
NTFY_RETRY_DELAY_SECONDS="1.0"
```

### Prioritäten über Keywords

`NTFY_PRIORITY_KEYWORDS` arbeitet **case-insensitive**. `MANV`, `manv` oder `ManV` werden gleich behandelt.

```env
NTFY_DEFAULT_PRIORITY="3"
NTFY_PRIORITY_KEYWORDS="Probealarm=1,MANV=4"
```

### Cluster / HA (mehrere Standorte)

- Der Node mit der höchsten `NODE_PRIORITY` ist aktiv und sendet.
- Andere Nodes bleiben im Standby.
- Bei gleicher Priorität entscheidet `NODE_ID`.

Beispiel:

```env
NODE_ID="gateway-standort-a"
NODE_PRIORITY="100"
PEER_NODES="10.8.0.12:8081,gateway-b.example.de:8081"
CLUSTER_SHARED_TOKEN="<optional-shared-secret>"
```

---

## Webhook-Nutzung

Webhook-Funktion aktivieren:

```env
WEBHOOK_ENABLED="true"
WEBHOOK_PORT="8080"
WEBHOOK_PATH="/webhook/alarm"
WEBHOOK_TRIGGER_PATH="/webhook/trigger"
WEBHOOK_TOKEN="<optional-token>"
```

### Endpunkte (Beispiel)

Bei `WEBHOOK_PORT=8080`, `HEALTH_PORT=8081`:

- POST JSON: `http://<HOST>:8080/webhook/alarm`
- GET Trigger: `http://<HOST>:8080/webhook/trigger?...`
- UI: `http://<HOST>:8080/`
- Health: `http://<HOST>:8081/healthz`
- Metrics: `http://<HOST>:8081/metrics`

### Beispiel-Requests

POST:

```bash
curl -X POST "http://<HOST>:8080/webhook/alarm" \
  -H "Content-Type: application/json" \
  -d '{"title":"MANV extern","text":"Alarm von Standort B","address":"Musterstr. 1","priority":4}'
```

GET-Trigger:

```bash
curl "http://<HOST>:8080/webhook/trigger?title=Einsatz%20extern&text=URL%20Trigger&address=Hauptstrasse%201&priority=4"
```

---

## Replay-Schutz für Webhooks (optional)

Wenn Webhooks aus externen Netzen kommen, solltest du Replay-Schutz aktivieren:

```env
WEBHOOK_REPLAY_PROTECTION="true"
WEBHOOK_HMAC_SECRET="<secret>"
WEBHOOK_MAX_SKEW_SECONDS="120"
```

Dann muss der Aufruf einen Timestamp (`ts`) und eine Signatur (`sig`) enthalten
(oder die Header `X-Webhook-Timestamp` und `X-Webhook-Signature`).

---

## Betrieb, Updates, Deinstallation

### Update (bestehende Installation aktualisieren)

```bash
cd /pfad/zu/divera-ntfy-gateway
git pull
sudo bash scripts/update.sh
sudo systemctl status alarm-gateway
```

Damit wird der aktuelle Stand eingespielt und der Dienst aktualisiert.

### Deinstallation (alles wieder entfernen)

```bash
cd /pfad/zu/divera-ntfy-gateway
sudo bash scripts/uninstall.sh
```

Optional: Wenn du auch die Konfiguration löschen möchtest:

```bash
sudo rm -rf /etc/alarm-gateway
```

Danach ist der Gateway-Dienst entfernt.

---

## Troubleshooting

- **Dienst startet nicht:**
  - `systemctl status alarm-gateway`
  - `journalctl -u alarm-gateway -n 200 --no-pager`
- **Keine Push-Nachrichten:**
  - `NTFY_URL`, `NTFY_TOPIC`, `NTFY_AUTH_TOKEN` prüfen
  - Erreichbarkeit des ntfy-Servers testen
- **Cluster sendet doppelt:**
  - `NODE_ID` je Node eindeutig setzen
  - `NODE_PRIORITY` sauber abstimmen
  - `PEER_NODES` inkl. korrektem Health-Port prüfen

---

## Projektstruktur (kurz)

- `alarm_gateway.py` – Hauptanwendung
- `scripts/install.sh` – Installation als systemd-Service
- `scripts/update.sh` – Update
- `scripts/uninstall.sh` – Deinstallation
- `systemd/alarm-gateway.service` – systemd Unit
- `tests/` – automatisierte Tests

Wenn du möchtest, kann ich als nächsten Schritt auch eine **komplette Beispiel-Konfiguration für Single-Node** und eine **für 2-Node-HA** direkt in die README ergänzen.

---

## Haftungsausschluss

Dieses Projekt ist ein **privates Freizeit-/Hobbyprojekt**.
Die Nutzung erfolgt vollständig **auf eigene Verantwortung**.

Es wird **keine Haftung** übernommen, insbesondere nicht für:

- direkte oder indirekte Schäden,
- Datenverlust,
- Fehlalarme, ausbleibende Alarme oder verspätete Benachrichtigungen,
- Folgeschäden durch Fehlkonfiguration, Ausfall von Drittanbietern (z. B. DiVeRa/ntfy) oder Systemstörungen.

Bitte prüfe das Verhalten vor dem produktiven Einsatz gründlich in einer Testumgebung und sorge für geeignete Fallback-Prozesse.
