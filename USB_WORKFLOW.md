# MK7BoostGauge — USB-primary workflow

> **Mode opératoire principal** : tu accèdes au Pi via câble USB depuis ton PC.
> Le WiFi du Pi reste 100% dédié à l'AP `MK7-BoostGauge` pour ton téléphone.

---

## 🧱 One-time setup (1× par installation)

### A. Sur le Pi (via SSH WiFi initial OU écran branché) :

```bash
cd ~/MK7BoostGauge
git pull
sudo bash pi_setup/setup_usb_mode.sh
# Le Pi rebootera automatiquement
```

Ce script fait :
1. Active USB Ethernet gadget (modifie `/boot/firmware/config.txt` + `cmdline.txt`)
2. Désactive l'autoupdate WiFi au boot
3. Reboot

### B. Sur ton PC Windows (1× après le reboot du Pi) :

**B1 — Autoriser PowerShell à exécuter les scripts** (Windows bloque par défaut)

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# Tape O puis Enter pour confirmer
```

> Pratique standard recommandée par Microsoft. Tu ne le refais jamais.

**B2 — Setup SSH key**

```powershell
cd C:\Users\AntoineLagrandeur\MK7BoostGauge
git pull
.\setup_ssh_key.ps1   # Setup SSH key auth (tape ton password Pi UNE fois)
```

→ À partir de maintenant, plus aucun mot de passe à taper pour les updates.

> ⚠️ **Si tu vois "Impossible de charger le fichier ... car l'exécution de scripts est désactivée"** :
> tu n'as pas fait l'étape B1. Lance la commande `Set-ExecutionPolicy` ci-dessus puis réessaie.

---

## 🚀 Workflow daily (à chaque fois que tu veux mettre à jour)

### Étape 1 — Branche le câble USB

- Une extrémité dans le **port DATA du Pi** (le port USB, PAS le PWR)
- L'autre dans ton PC

Le Pi prend ~30 sec pour monter l'interface usb0.

### Étape 2 — Update en une commande

```powershell
cd C:\Users\AntoineLagrandeur\MK7BoostGauge
.\update_pi.ps1
```

Le script :
1. Pull les derniers commits depuis GitHub vers TON PC
2. Package les fichiers en tarball
3. Envoie le tarball au Pi via USB (scp)
4. Extrait sur le Pi
5. Run `pip install --upgrade` si requirements.txt a changé
6. Restart le service `boostgauge`
7. Affiche le status

**Durée totale** : ~10-15 secondes.

### Étape 3 — Tester sur ton téléphone

- WiFi `MK7-BoostGauge` (password `boost123`)
- http://192.168.4.1

(Le PC peut aussi accéder via USB : http://boostgauge.local)

---

## 🔄 Quand tu veux modifier le code

### Option A — Édition rapide sur PC + update_pi.ps1

1. Édite dans `C:\Users\AntoineLagrandeur\MK7BoostGauge\` (VSCode, Notepad++, etc.)
2. **Optionnel** : commit + push vers GitHub
3. Lance `.\update_pi.ps1` → envoie tes changements (locaux + GitHub) au Pi

### Option B — Édition directe sur Pi via SSH

```powershell
ssh pi@boostgauge.local
cd ~/MK7BoostGauge
nano app/boost_logic.py
# Édite, Ctrl+O, Enter, Ctrl+X
sudo systemctl restart boostgauge
```

### Option C — VSCode Remote-SSH (le plus confortable long terme)

1. Installe VSCode + extension "Remote - SSH"
2. F1 → `Remote-SSH: Connect to Host` → `pi@boostgauge.local`
3. Édite directement sur le Pi avec autocomplétion, terminal intégré, etc.

---

## 🔍 Diagnostic

### Le Pi est-il accessible via USB ?

```powershell
ping boostgauge.local
ssh pi@boostgauge.local
```

Si ça timeout :
- Vérifier câble dans le port DATA (pas PWR)
- Vérifier Pi booté (LED verte stable)
- `Get-NetAdapter | Where { $_.InterfaceDescription -like "*Gadget*" }` doit retourner Status=Up
- Si Status=Disconnected : driver RNDIS pas installé (voir USB_WORKFLOW troubleshoot)

### Le service tourne-t-il bien ?

```powershell
ssh pi@boostgauge.local "systemctl status boostgauge"
```

### Health check complet

```powershell
ssh pi@boostgauge.local "bash ~/MK7BoostGauge/pi_setup/check_health.sh"
```

---

## 🔙 Revenir au mode WiFi auto-update (si besoin un jour)

```bash
ssh pi@boostgauge.local
sudo systemctl enable boostgauge-autoupdate.service
sudo rm /var/lib/boostgauge/disable_autoupdate
# Re-add boostgauge-autoupdate.service to After/Wants in boostgauge.service:
sudo bash ~/MK7BoostGauge/pi_setup/setup.sh
sudo reboot
```

---

## 📋 Récapitulatif

| Action | Comment |
|---|---|
| Premier setup Pi | `sudo bash pi_setup/setup_usb_mode.sh` (sur Pi) |
| Setup SSH key PC | `.\setup_ssh_key.ps1` (1×, sur PC) |
| **Daily : mettre à jour le Pi** | **Branche USB, `.\update_pi.ps1`** |
| Modifier code sur PC | Édite localement → `.\update_pi.ps1` |
| Modifier code sur Pi | `ssh pi@boostgauge.local`, `nano ...`, `sudo systemctl restart boostgauge` |
| Voir UI | Téléphone → `MK7-BoostGauge` → `http://192.168.4.1` |
| Health check | `ssh pi@boostgauge.local "bash ~/MK7BoostGauge/pi_setup/check_health.sh"` |

→ **WiFi du Pi = 100% AP**, USB = 100% mode dev/update. Clean separation.
