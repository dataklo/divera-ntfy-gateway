# divera-ntfy-gateway

Pollt die DiVeRa-API auf neue Alarmierungen und sendet Push-Benachrichtigungen an einen ntfy-Topic.
Damit bekommst du Push auf Android **ohne** Google Play Services/FCM.

## Was wird hier gemacht?

- `GET https://divera247.com/api/v2/alarms?accesskey=...` (Polling)  
  DiVeRa-Doku: v2/alarms benötigt `accesskey`.  
- Bei einem neuen Alarm (Dedup per Fingerprint/State-File) wird an ntfy publiziert:
  `POST {NTFY_URL}/{NTFY_TOPIC}`

### Quellen
- DiVeRa v2/alarms: https://help.divera247.com/pages/viewpage.action?pageId=96240316
- ntfy Phone/UnifiedPush: https://docs.ntfy.sh/subscribe/phone/
- UnifiedPush Distributors Übersicht: https://unifiedpush.org/users/distributors/
- NextPush Distributor + Nextcloud app (uppush): https://unifiedpush.org/users/distributors/nextpush/

---

## 1) Installation auf Server (Debian/Ubuntu, z.B. Proxmox LXC)

### Voraussetzungen
- Debian/Ubuntu (VM oder LXC)
- Internet-Zugriff zum Abruf der DiVeRa API
- DiVeRa Accesskey
- Ein Push-Distributor (empfohlen: **ntfy** oder **NextPush**)

### Install
1. Repo auf den Zielhost kopieren oder klonen
2. Install-Skript ausführen:

```bash
sudo bash scripts/install.sh
```

3. Konfiguration setzen (Secrets liegen **nicht** im Repo):

```bash
sudo nano /etc/alarm-gateway/alarm-gateway.env
```

4. Dienst neu starten:

```bash
sudo systemctl restart alarm-gateway
```

5. Logs:

```bash
sudo journalctl -u alarm-gateway -f
```

### Update

```bash
sudo bash scripts/update.sh
```

### Uninstall

```bash
sudo bash scripts/uninstall.sh
```

> Achtung: `uninstall.sh` löscht auch `/etc/alarm-gateway/` (Secrets) und `/var/lib/alarm-gateway/` (State).

---

## 2) Push einrichten (Smartphone)

Du hast zwei sinnvolle Wege:

### A) Empfohlen: **ntfy** als UnifiedPush-Distributor (Nextcloud muss nichts tun)

1. Installiere **ntfy** auf dem Smartphone (F-Droid oder Play Store).
2. (Optional) In ntfy deinen eigenen Server eintragen, falls du selfhostest.
3. Abonniere das Topic `{NTFY_TOPIC}` in ntfy (oder nutze ntfy als UnifiedPush-Distributor für andere Apps).

Info: ntfy kann als UnifiedPush-Distributor arbeiten.  
Siehe: https://docs.ntfy.sh/subscribe/phone/

**Test:**
```bash
curl -d "Test vom alarm-gateway" https://<ntfy-server>/<topic>
```

### B) Wenn du explizit Nextcloud nutzen willst: **NextPush** + Nextcloud App `uppush`

Das ist sinnvoll, wenn du schon Nextcloud hast und Push darüber laufen soll.

#### Nextcloud (Server)
1. Als Nextcloud-Admin die App **UnifiedPush Provider** installieren: `uppush`  
   (NextPush erfordert die Nextcloud-App `uppush`.)
2. Falls du Nextcloud per Docker/AIO betreibst: Redis ist empfehlenswert (NextPush/Nextcloud-Hintergrundjobs).

Quelle: NextPush benötigt `uppush` (Nextcloud App).  
Siehe: https://unifiedpush.org/users/distributors/nextpush/

#### Smartphone (Android)
1. Installiere die App **NextPush** (Distributor).
2. In NextPush die URL deines Nextcloud-Servers eintragen und koppeln.
3. In UnifiedPush-fähigen Apps NextPush als Distributor auswählen.

Anleitung: https://unifiedpush.org/users/distributors/nextpush/

> Hinweis: Für **dieses** Projekt (Server → Push) brauchst du Nextcloud nicht zwingend.  
> Nextcloud/NextPush sind nur nötig, wenn du UnifiedPush über Nextcloud als Provider betreiben willst.

---

## 3) Konfiguration

In `/etc/alarm-gateway/alarm-gateway.env`:

Pflicht:
- `DIVERA_ACCESSKEY`
- `NTFY_URL`
- `NTFY_TOPIC`

Optional:
- `POLL_SECONDS` (Standard: 20)
- `STATE_FILE` (Standard: /var/lib/alarm-gateway/state.json)
- `VERIFY_TLS` (Standard: true)

---

## Troubleshooting

### Keine Pushs
- Funktioniert `curl -d test {NTFY_URL}/{NTFY_TOPIC}`?
- Logs prüfen: `journalctl -u alarm-gateway -f`
- Zugriff auf DiVeRa:  
  `curl "https://divera247.com/api/v2/alarms?accesskey=DEIN_KEY"`

### Doppelte Pushs / Reboots
- State liegt unter `/var/lib/alarm-gateway/state.json` (Fingerprint der letzten Alarmierung)

---

## Lizenz
MIT (oder nach Wunsch anpassen)
