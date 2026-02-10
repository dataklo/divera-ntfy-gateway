# divera-ntfy-gateway

Pollt die DiVeRa-API auf neue Alarmierungen und sendet Push-Benachrichtigungen an einen ntfy-Topic.
Damit bekommst du Push auf Android **ohne** Google Play Services / FCM.

**Zielbild (Selfhosted):**
- Du betreibst Nextcloud selbst
- Du nutzt z. B. LineageOS ohne Google-Dienste
- Du möchtest DiVeRa-Alarmierungen trotzdem als Push auf dem Handy erhalten

Genau dafür ist dieses Projekt gedacht: **DiVeRa → Gateway → UnifiedPush-Distributor (ntfy oder NextPush) → Smartphone**.

Repository:
https://github.com/dataklo/divera-ntfy-gateway

---

## Was macht das Projekt?

- Regelmäßiges Polling der DiVeRa-API  
  `GET https://divera247.com/api/v2/alarms?accesskey=...`
- Erkennen neuer Alarmierungen (Dedup per Fingerprint/State)
- Versand einer Push-Benachrichtigung über **UnifiedPush** (z. B. ntfy oder NextPush)

---


## 0) Schnellstart für **blanke LXC** (Proxmox, Debian 12)

Wenn dein Container wirklich „frisch“ ist, geh exakt so vor:

1. Container starten und als `root` einloggen
2. Basis-Pakete installieren:
```bash
apt update
apt install -y git ca-certificates curl
```
3. Repository klonen:
```bash
cd /root
git clone https://github.com/dataklo/divera-ntfy-gateway.git
cd divera-ntfy-gateway
```
4. Installation ausführen:
```bash
bash scripts/install.sh
```
5. Konfiguration setzen:
```bash
nano /etc/alarm-gateway/alarm-gateway.env
```
Mindestens diese Werte eintragen:
- `DIVERA_ACCESSKEY`
- und **eine** Push-Variante:
  - `NTFY_URL` + `NTFY_TOPIC` **oder**
  - `UPPUSH_ENDPOINT`

6. Dienst neu starten und prüfen:
```bash
systemctl restart alarm-gateway
systemctl status alarm-gateway --no-pager
journalctl -u alarm-gateway -n 50 --no-pager
```

Wenn `active (running)` angezeigt wird und keine Fehler im Journal stehen, läuft das Gateway korrekt.

---

## 1) Installation auf dem Server (Debian/Ubuntu, Proxmox LXC/VM)

### Voraussetzungen
- Debian/Ubuntu (VM oder LXC)
- Internetzugang
- DiVeRa Accesskey
- Push-Distributor (**ntfy** oder **NextPush**)
- systemd im Container aktiv (für den `alarm-gateway` Service)

> Tipp für Proxmox-LXC: Debian 12 Template + funktionierendes DNS reichen normalerweise aus.

### Empfohlene Selfhosted-Setups

#### Setup 1: Vollständig selfhosted mit Nextcloud
- **Server:**
  - `divera-ntfy-gateway` (dieses Projekt)
  - Nextcloud mit `uppush` (UnifiedPush Provider)
- **Smartphone (LineageOS):**
  - NextPush App (Distributor)
- **Vorteil:** Alles in deiner eigenen Infrastruktur

#### Setup 2: Selfhosted mit eigenem ntfy
- **Server:**
  - `divera-ntfy-gateway`
  - eigener ntfy-Server
- **Smartphone (LineageOS):**
  - ntfy App
- **Vorteil:** Sehr simpel und robust

### Repository klonen
```bash
git clone https://github.com/dataklo/divera-ntfy-gateway.git
cd divera-ntfy-gateway
```

### Installation
```bash
sudo bash scripts/install.sh
```

### Konfiguration
```bash
sudo nano /etc/alarm-gateway/alarm-gateway.env
```

Danach Dienst neu starten:
```bash
sudo systemctl restart alarm-gateway
```

Logs anzeigen:
```bash
sudo journalctl -u alarm-gateway -f
```

---

## 2) Update / Entfernen

### Update
```bash
cd divera-ntfy-gateway
git pull
sudo bash scripts/update.sh
```

### Uninstall
```bash
sudo bash scripts/uninstall.sh
```

⚠️ Achtung: Entfernt auch Secrets und State-Daten.

---

## 3) Smartphone-Einrichtung (sehr wichtig)

### Grundprinzip
Für UnifiedPush brauchst du **immer einen Distributor auf dem Smartphone**.  
Apps selbst empfangen **keine Pushs direkt**, sondern über diesen Distributor.

Du hast **zwei sinnvolle Varianten**:

---

### Variante A (empfohlen): ntfy als Push-Distributor

**Was brauchst du auf dem Smartphone?**
- ✅ **ntfy App** (F-Droid oder Play Store)
- ❌ **keine** Nextcloud-App nötig
- ❌ **kein** Google / FCM nötig

**Einrichtung**
1. ntfy App installieren
2. Falls selfhosted: eigenen ntfy-Server eintragen
3. Topic abonnieren (z. B. `fw-alarme-x9k3p`)

Test:
```bash
curl -d "Test vom divera-gateway" https://ntfy.example.com/fw-alarme-x9k3p
```

➡️ Wenn die Nachricht ankommt, ist alles korrekt.

Infos:
https://docs.ntfy.sh/subscribe/phone/

---

### Variante B: Nextcloud + NextPush (UnifiedPush über Nextcloud)

Diese Variante nutzt deine **Nextcloud als Push-Backend**.

#### Auf dem Nextcloud-Server
- App **UnifiedPush Provider (`uppush`)** installieren
- Hintergrundjobs / Cron aktiv
- Redis empfohlen

#### Auf dem Smartphone
- ✅ **NextPush App**
- ❌ **keine** ntfy-App nötig
- ❌ **kein** Google / FCM nötig

**Wichtig**
- NextPush ist **nur der Distributor**
- Das divera-ntfy-gateway sendet trotzdem an ntfy **oder** an einen UnifiedPush-Endpunkt
- Nextcloud ist **nicht zwingend erforderlich**, nur wenn du UnifiedPush darüber betreiben willst

Infos:
https://unifiedpush.org/users/distributors/nextpush/

---

### Schritt-für-Schritt: nur Nextcloud + uppush (ohne ntfy)

Wenn du bereits eine öffentlich erreichbare Nextcloud mit `uppush` hast, gehe so vor:

1. **NextPush auf dem Smartphone installieren** und mit deiner Nextcloud verbinden.
2. In NextPush einen Push-Endpoint erzeugen/kopieren (URL im Stil `https://<cloud>/index.php/apps/uppush/push/<token>`).
3. Auf dem Gateway die Env-Datei öffnen:
```bash
sudo nano /etc/alarm-gateway/alarm-gateway.env
```
4. Nur folgende Push-Werte setzen:
```env
DIVERA_ACCESSKEY="DEIN_DIVERA_KEY"
UPPUSH_ENDPOINT="https://nextcloud.example.com/index.php/apps/uppush/push/<endpoint-token>"
# optional:
# UPPUSH_AUTH_HEADER="Bearer <token>"
```
5. Falls vorhanden, alte ntfy-Werte auskommentieren/leer lassen (`NTFY_URL`, `NTFY_TOPIC`), damit klar nur uppush genutzt wird.
6. Dienst neu starten und Logs prüfen:
```bash
sudo systemctl restart alarm-gateway
sudo systemctl status alarm-gateway --no-pager
sudo journalctl -u alarm-gateway -n 50 --no-pager
```
7. Endpoint testen:
```bash
curl -X POST -d "Test vom divera-gateway" "https://nextcloud.example.com/index.php/apps/uppush/push/<endpoint-token>"
```

Wenn der Curl-Test ankommt und der Service dauerhaft `active (running)` bleibt, ist die uppush-only Konfiguration fertig.

## 4) Brauche ich neben NextPush noch etwas auf dem Smartphone?

**Kurzantwort:** ❌ Nein

Wenn du **NextPush** nutzt:
- NextPush = dein UnifiedPush-Distributor
- Mehr brauchst du **nicht**
- Keine Google-Dienste
- Keine zusätzliche App

Wenn du **ntfy** nutzt:
- ntfy App = Distributor **und** Client

---

## 5) Konfiguration

Datei:
```bash
/etc/alarm-gateway/alarm-gateway.env
```

Pflicht:
- `DIVERA_ACCESSKEY`
- und **eine** Push-Variante:
  - **Variante ntfy:** `NTFY_URL` + `NTFY_TOPIC`
  - **Variante uppush/UnifiedPush-Endpoint:** `UPPUSH_ENDPOINT`

Optional:
- `POLL_SECONDS` (Standard: 20)
- `STATE_FILE`
- `NTFY_PRIORITY`
- `UPPUSH_AUTH_HEADER` (optional `Authorization` Header für Endpoint-Auth)
- `VERIFY_TLS`

### Beispiel A: ntfy
```env
DIVERA_ACCESSKEY="DEIN_DIVERA_KEY"
NTFY_URL="https://ntfy.sh"
NTFY_TOPIC="dein-zufaelliger-topic"
```

### Beispiel B: Nextcloud/uppush
```env
DIVERA_ACCESSKEY="DEIN_DIVERA_KEY"
UPPUSH_ENDPOINT="https://nextcloud.example.com/index.php/apps/uppush/push/<dein-endpoint-token>"
# Optional, falls dein Endpoint Auth verlangt:
# UPPUSH_AUTH_HEADER="Bearer <token>"
```

---

## 5b) Mini-Checkliste: „Bin ich fertig?“

- `systemctl status alarm-gateway` zeigt **active (running)**
- in `journalctl -u alarm-gateway -f` erscheinen **keine dauerhaften ERRORs**
- Testnachricht kommt auf dem Smartphone an:
```bash
# ntfy
curl -d "Test vom divera-gateway" https://DEIN-NTFY-SERVER/DEIN-TOPIC

# uppush endpoint
curl -X POST -d "Test vom divera-gateway" "https://nextcloud.example.com/index.php/apps/uppush/push/<endpoint-token>"
```

Wenn alle drei Punkte passen, ist die Installation auf einer blanken LXC in der Regel sauber abgeschlossen.

---

## 6) Troubleshooting

### Keine Pushs
- Teste den Push-Zielpfad manuell (`curl -d test ...`)
  - ntfy: `curl -d test https://DEIN-NTFY-SERVER/DEIN-TOPIC`
  - uppush endpoint: `curl -X POST -d test https://.../apps/uppush/push/<endpoint-token>`
- Logs prüfen: `journalctl -u alarm-gateway -f`
- API testen:
```bash
curl "https://divera247.com/api/v2/alarms?accesskey=DEIN_KEY"
```

### Doppelte Pushs
- State-Datei prüfen:
```bash
/var/lib/alarm-gateway/state.json
```

---

## Lizenz
MIT
