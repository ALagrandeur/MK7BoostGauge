# MK7BoostGauge v3

Boost gauge override pour cluster VW MK7 Alltrack 2017. Architecture **PC + Pi**.

## 🧱 Architecture

```
┌─────────────────────────────────────┐         ┌────────────────────────────┐
│  PC Windows                         │         │  Pi Zero 2W                │
│                                     │         │                            │
│  Flask local (localhost:8080)       │   WiFi  │  Daemon HTTP listener      │
│  UI dans navigateur                 │  HTTP   │  Hot apply config          │
│  Bouton [Envoyer au PI]             │ ─POST─► │  Drive CAN HAT             │
│  Storage: pc_config.json (projet)   │         │  TX Motor_09 conditionnel  │
└─────────────────────────────────────┘         └────────────────────────────┘
```

**Principe** :
- Le **PC** est le cerveau de config : sliders, test mode, état live.
- Le **Pi** exécute : reçoit config par HTTP, applique à chaud, drive le cluster.
- **Aucun GitHub sur le Pi**, aucun autoupdate, aucune web UI sur le Pi.
- Les deux sont sur **le même WiFi maison**. Discovery via mDNS `boostgauge.local`.
- Pi tourne en autonomie avec dernière config reçue si pas de WiFi (mode voiture).

## 📁 Structure

```
MK7BoostGauge/
├── pc/                      ← TOUT ce qui tourne sur le PC
│   ├── launch.py            ← Entry point lancé par le raccourci .lnk
│   ├── install_shortcut.py  ← Crée le raccourci bureau (1 fois)
│   ├── requirements.txt
│   ├── app/
│   │   ├── server.py        ← Flask local + endpoints
│   │   ├── config.py        ← pc_config.json persistence
│   │   └── pi_client.py     ← HTTP client vers le Pi
│   └── static/
│       ├── index.html       ← UI simplifiée
│       ├── style.css
│       └── app.js
│
├── pi/                      ← TOUT ce qui tourne sur le Pi
│   ├── daemon.py            ← Service principal (HTTP + CAN)
│   ├── vw_signals.py        ← Decode gear, build Motor_09, dead zone
│   ├── can_manager.py       ← Wrapper python-can dual MCP2515
│   ├── setup_pi.sh          ← Script setup initial (1 fois)
│   └── requirements.txt
│
├── tests/
│   └── test_vw_signals.py   ← Tests unitaires logique CAN
│
├── archive/                 ← Ancienne v1/v2 archivée (référence)
└── pc_config.json           ← Settings persistés du PC (créé au 1er run)
```

## 🚀 Installation

### Phase A — PC (5 min, 1 fois)

```powershell
cd C:\Users\AntoineLagrandeur
git clone https://github.com/ALagrandeur/MK7BoostGauge.git
cd MK7BoostGauge

# Installer Python deps PC
python -m pip install -r pc/requirements.txt

# Créer raccourci bureau
python pc/install_shortcut.py
```

→ Tu vois `MK7BoostGauge.lnk` sur ton bureau Windows.

### Phase B — Pi (10 min, 1 fois)

**Pré-requis hardware** : HAT WaveShare 2-CH CAN MCP2515 monté sur les pins GPIO du Pi Zero 2W. Termination jumpers : **enlevés** si Pi sera dans la voiture (cluster fait déjà la terminaison), **gardés** si Pi sur bench isolé.

#### Étape B-1 — Flash SD (déjà fait par l'utilisateur)

Raspberry Pi Imager :
- OS : **Raspberry Pi OS Lite (64-bit)**
- Settings ⚙️ :
  - Hostname : `boostgauge`
  - User : `pi` / Password : `boost123`
  - WiFi : ton SSID maison + password (case sensitive!)
  - **Wireless LAN country : CA** (sinon WiFi ne s'active pas)
  - SSH activé (password auth)
  - Locale : ton fuseau

#### Étape B-2 — Insérer SD, brancher alim, attendre 90 sec

LED verte du Pi : clignote au boot, devient stable quand prête.

#### Étape B-3 — SSH depuis PC + install (1 fois)

```bash
ssh pi@boostgauge.local
# Si ça plante avec "host not found", trouve l'IP via ton routeur
# puis: ssh pi@192.168.X.X

git clone https://github.com/ALagrandeur/MK7BoostGauge.git
cd MK7BoostGauge/pi
sudo bash setup_pi.sh
```

Le script installe :
- Dépendances apt (python venv, can-utils, git)
- Overlays MCP2515 dans `/boot/firmware/config.txt` (**SPI à 1MHz** = fix kernel oops Pi Zero 2W)
- Service systemd `boostgauge-can-up.service` (auto can0/can1 à 500 kbps)
- Venv Python + Flask
- Service systemd `boostgauge-daemon.service` (démarre à chaque boot)

Quand tu vois `==> Setup complete!`, faire :
```bash
sudo reboot
```

#### Étape B-4 — Après reboot : vérification

```bash
ssh pi@boostgauge.local
bash ~/MK7BoostGauge/pi/check_health.sh
```

Script color-coded qui vérifie :
1. ✅ Module MCP2515 chargé sans kernel oops
2. ✅ can0/can1 UP à 500 kbps
3. ✅ Services systemd enabled + daemon active
4. ✅ Endpoints HTTP répondent (/ping, /status)
5. ✅ Config file owned by pi:pi
6. ℹ️ Sniff CAN0 3 sec (compte les frames si cluster branché)

Si tout est vert → **Pi 100% opérationnel**. Tu peux le débrancher l'écran et lui parler seulement via le PC via WiFi.

## 🚀 Utilisation daily

```
1. Allume le Pi (alim 12V)
2. Sur PC : double-clic "MK7BoostGauge" sur bureau
   → Navigateur s'ouvre auto sur http://localhost:8080
3. Modifie les sliders (MAP min/max, scale, offset, Hz)
4. Clic "Envoyer au Pi" → cluster réagit immédiat (hot apply)
5. Mode test : actives + clic preset → envoie instantanément
```

## ✨ Features de l'UI

| Card | Description |
|---|---|
| 🎯 **Configuration cluster** | 5 sliders : MAP min/max, scale, offset, Hz |
| 🧪 **Mode Test Cluster** | Force température fixe (50/80/90/110/130°C), envoi LIVE sur chaque clic preset |
| 📡 **État live du Pi** | Polling 1Hz : levier, mode (BOOST/TEMP/TEST/WAITING), byte envoyé, temp |
| 📤 **Envoyer au PI** | Gros bouton rouge qui push la config au Pi |
| 🔌 **Connexion Pi** | Hostname/port + bouton test |

**Réglages fixés en interne** (cohérent avec calibration cluster MK7) :
- Temp min = 50°C, Temp max = 130°C
- Formule linéaire avec **saut zone morte 80-110°C** (sinon aiguille fige au milieu)
- Sécurité airbag (0x040, 0x572, 0x585) hardcoded bloquée

## 🔍 Diagnostic

| Test | Commande |
|---|---|
| Pi joignable ? | `ping boostgauge.local` (sur PC) |
| API Pi répond ? | `curl http://boostgauge.local:8765/ping` |
| État Pi ? | `curl http://boostgauge.local:8765/status` |
| Tests automatisés | `python -m pytest tests/ -v` |

## 🧪 Tests

```powershell
python -m pytest tests/ -v
# 26 tests sur vw_signals (mapping, dead zone, lever decode, etc.)
```

## ⚠️ Limites connues

- **MAP source** : pas encore implémenté côté Pi (TX Motor_09 utilise mid-range MAP en mode BOOST par défaut). À ajouter quand on connectera CAN1 à PCM ou OBD-II.
- **CAN1 (PCM/OBD)** : matériel branché mais pas utilisé en v3. Reservé pour évolution future.
- **Bluetooth** : pas implémenté en v3. Si jamais le Pi est hors WiFi, la communication PC↔Pi ne marche plus jusqu'au retour en zone WiFi.

## 📜 Historique

- **v1** (archivée) : ESP32 + GVRET bench
- **v2** (archivée) : Pi avec web UI + autoupdate GitHub + AP fallback (trop complexe)
- **v3** (cette version) : PC = cerveau, Pi = exécuteur. Simple, robuste.

Ancien code conservé dans `archive/` pour référence.
