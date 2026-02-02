# divera-ntfy-gateway

Pollt die DiVeRa-API auf neue Alarmierungen und sendet Push-Benachrichtigungen an einen ntfy-Topic.
Damit bekommst du Push auf Android **ohne** Google Play Services / FCM.

Repository:
https://github.com/dataklo/divera-ntfy-gateway

---

## Was macht das Projekt?

- Regelmäßiges Polling der DiVeRa-API  
  `GET https://divera247.com/api/v2/alarms?accesskey=...`
- Erkennen neuer Alarmierungen (Dedup per Fingerprint/State)
- Versand einer Push-Benachrichtigung über **UnifiedPush** (z. B. ntfy oder NextPush)

---

## 1) Installation auf dem Server (Debian/Ubuntu, Proxmox LXC/VM)

### Voraussetzungen
- Debian/Ubuntu (VM oder LXC)
- Internetzugang
- DiVeRa Accesskey
- Push-Distributor (**ntfy** oder **NextPush**)

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
- `NTFY_URL`
- `NTFY_TOPIC`

Optional:
- `POLL_SECONDS` (Standard: 20)
- `STATE_FILE`
- `NTFY_PRIORITY`
- `VERIFY_TLS`

---

## 6) Troubleshooting

### Keine Pushs
- Teste ntfy manuell (`curl -d test ...`)
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
