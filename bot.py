"""
FinancialJuice Discord Alert Bot
Scrapes red (breaking) headlines from financialjuice.com and pings Discord
"""

import asyncio
import aiohttp
import json
import logging
import os
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
#  CONFIG  (edit here or use environment vars)
# ─────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "VOTRE_WEBHOOK_URL_ICI")
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", "10"))   # secondes entre chaque check
FINANCIALJUICE_URL  = "https://www.financialjuice.com/home"
LOG_LEVEL           = os.getenv("LOG_LEVEL", "INFO")

# ─────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fj-bot")

seen_ids: set[str] = set()   # garde en mémoire les headlines déjà envoyés

# ──────────────────────────────────────────────────────────────
#  SCRAPING
# ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.financialjuice.com/",
}


async def fetch_headlines(session: aiohttp.ClientSession) -> list[dict]:
    """
    Retourne la liste des headlines rouges (breaking news) trouvés sur la page.
    Chaque item : {id, text, tags, time}
    """
    try:
        async with session.get(FINANCIALJUICE_URL, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                log.warning(f"HTTP {resp.status} depuis financialjuice.com")
                return []
            html = await resp.text()
    except Exception as e:
        log.error(f"Erreur de connexion : {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Financial Juice affiche ses headlines dans des <div class="headlineItem"> (ou similaires)
    # On cherche tous les éléments ayant une classe qui contient "red" ou "breaking"
    # et on extrait le texte + heure
    for item in soup.select("div.headlineItem, li.headlineItem, div[class*='headline']"):
        classes = " ".join(item.get("class", []))
        # Filtre rouge : la classe contient 'red', 'breaking', ou 'alert'
        is_red = any(k in classes.lower() for k in ("red", "breaking", "alert", "important"))
        if not is_red:
            # Certaines versions utilisent un span coloré à l'intérieur
            red_span = item.select_one("span.red, span.breaking, span[class*='red'], div[class*='red']")
            if not red_span:
                continue

        text = item.get_text(separator=" ", strip=True)
        if not text or len(text) < 10:
            continue

        # ID unique basé sur le texte normalisé
        uid = re.sub(r"\s+", " ", text).strip().lower()[:120]

        # Extraction des tags (Energy, USD, etc.)
        tags = [t.get_text(strip=True) for t in item.select("span.tag, a.tag, span[class*='tag']")]

        # Heure affichée
        time_el = item.select_one("span.time, span[class*='time'], div[class*='time']")
        time_str = time_el.get_text(strip=True) if time_el else datetime.now(timezone.utc).strftime("%H:%M")

        results.append({"id": uid, "text": text, "tags": tags, "time": time_str})

    # Fallback : certaines versions chargent les headlines via une API JSON interne
    if not results:
        results = await fetch_headlines_api(session)

    return results


async def fetch_headlines_api(session: aiohttp.ClientSession) -> list[dict]:
    """
    Tente de récupérer les headlines depuis l'endpoint API de Financial Juice.
    """
    api_urls = [
        "https://www.financialjuice.com/api/headlines",
        "https://www.financialjuice.com/feed",
    ]
    for url in api_urls:
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                ct = resp.headers.get("Content-Type", "")
                if "json" in ct:
                    data = await resp.json(content_type=None)
                    return parse_api_response(data)
                else:
                    text = await resp.text()
                    return parse_rss(text)
        except Exception:
            continue
    return []


def parse_api_response(data) -> list[dict]:
    items = []
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("items", data.get("headlines", data.get("data", [])))
    else:
        return []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        color = str(entry.get("color", entry.get("type", ""))).lower()
        importance = str(entry.get("importance", entry.get("priority", ""))).lower()
        is_red = color in ("red", "#ff0000", "1") or importance in ("high", "breaking", "red")
        if not is_red:
            continue
        text = entry.get("title", entry.get("text", entry.get("headline", "")))
        if not text:
            continue
        uid = re.sub(r"\s+", " ", text).strip().lower()[:120]
        items.append({
            "id": uid,
            "text": text,
            "tags": entry.get("tags", entry.get("categories", [])),
            "time": entry.get("time", entry.get("date", datetime.now(timezone.utc).strftime("%H:%M"))),
        })
    return items


def parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS/Atom si l'API retourne du XML."""
    soup = BeautifulSoup(xml_text, "xml")
    items = []
    for item in soup.find_all("item")[:20]:
        title = item.find("title")
        if not title:
            continue
        text = title.get_text(strip=True)
        uid = re.sub(r"\s+", " ", text).strip().lower()[:120]
        items.append({"id": uid, "text": text, "tags": [], "time": ""})
    return items


# ──────────────────────────────────────────────────────────────
#  DISCORD
# ──────────────────────────────────────────────────────────────
async def send_discord_alert(session: aiohttp.ClientSession, headline: dict):
    tags_str = "  •  ".join(headline["tags"]) if headline["tags"] else ""
    time_str = headline["time"] or datetime.now(timezone.utc).strftime("%H:%M UTC")

    embed = {
        "title": "🚨 BREAKING — Financial Juice",
        "description": f"**{headline['text']}**",
        "color": 0xE8002D,   # rouge FJ
        "fields": [],
        "footer": {"text": f"financialjuice.com  •  {time_str}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if tags_str:
        embed["fields"].append({"name": "Tags", "value": tags_str, "inline": False})

    payload = {
        "username": "FinancialJuice Alerts",
        "avatar_url": "https://www.financialjuice.com/favicon.ico",
        "content": "",
        "embeds": [embed],
    }

    try:
        async with session.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 204):
                log.info(f"✅ Envoyé : {headline['text'][:80]}")
            else:
                body = await resp.text()
                log.error(f"Discord erreur {resp.status}: {body[:200]}")
    except Exception as e:
        log.error(f"Impossible d'envoyer sur Discord : {e}")


# ──────────────────────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ──────────────────────────────────────────────────────────────
async def main():
    log.info("=== FinancialJuice Discord Bot démarré ===")
    log.info(f"Intervalle de scraping : {POLL_INTERVAL}s")

    if "VOTRE_WEBHOOK_URL_ICI" in DISCORD_WEBHOOK_URL:
        log.error("⚠️  Configure DISCORD_WEBHOOK_URL avant de lancer le bot !")
        return

    async with aiohttp.ClientSession() as session:
        # Premier passage : charge les headlines existants sans les envoyer
        log.info("Chargement initial des headlines (pas d'envoi)…")
        initial = await fetch_headlines(session)
        for h in initial:
            seen_ids.add(h["id"])
        log.info(f"{len(seen_ids)} headlines existants ignorés.")

        while True:
            await asyncio.sleep(POLL_INTERVAL)
            headlines = await fetch_headlines(session)

            new_ones = [h for h in headlines if h["id"] not in seen_ids]
            if new_ones:
                log.info(f"{len(new_ones)} nouvelle(s) alerte(s) rouge(s) trouvée(s)")
                for h in new_ones:
                    seen_ids.add(h["id"])
                    await send_discord_alert(session, h)
                    await asyncio.sleep(0.5)   # évite le rate-limit Discord
            else:
                log.debug("Pas de nouveau headline rouge.")


if __name__ == "__main__":
    asyncio.run(main())
