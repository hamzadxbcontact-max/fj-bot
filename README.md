# 🚨 FinancialJuice Discord Bot

Ping automatique sur Discord dès qu'une alerte **rouge** (breaking news) apparaît sur [financialjuice.com](https://www.financialjuice.com).

---

## ⚡ Installation rapide (3 étapes)

### 1. Créer un Webhook Discord
1. Va dans ton serveur Discord → **Paramètres du salon** → **Intégrations** → **Webhooks**
2. Clique **Nouveau webhook**, donne-lui un nom (ex: `FJ Alerts`)
3. Copie l'**URL du webhook**

### 2. Configurer le bot

Crée un fichier `.env` dans le dossier du bot :

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXXX/YYYY
POLL_INTERVAL=10
```

> `POLL_INTERVAL` = secondes entre chaque check (10s recommandé)

### 3. Lancer

**Option A – Python direct :**
```bash
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python bot.py
```

**Option B – Docker (recommandé pour tourner 24/7) :**
```bash
docker compose up -d
```

---

## 📨 Format du message Discord

```
@here
🚨 BREAKING — Financial Juice
US and Iran reach agreement on 60-day MOU to extend truce
Tags: Energy  •  US Bonds  •  USD
financialjuice.com  •  16:17 UTC
```

---

## ⚙️ Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(obligatoire)* | URL du webhook Discord |
| `POLL_INTERVAL` | `10` | Secondes entre chaque scraping |
| `LOG_LEVEL` | `INFO` | `DEBUG` pour plus de détails |

---

## 🔧 Dépannage

- **Aucun message reçu** : vérifie que le webhook est bien configuré et que la page FJ charge correctement
- **Rate limit Discord** : augmente `POLL_INTERVAL` à 15-20s
- **Headlines manqués** : passe `LOG_LEVEL=DEBUG` pour voir ce qui est détecté
