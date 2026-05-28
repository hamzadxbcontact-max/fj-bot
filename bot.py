"""
FinancialJuice Discord Alert Bot
Utilise le flux RSS de financialjuice.com pour détecter les alertes rouges
"""

import asyncio
import aiohttp
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from hashlib import md5

# ─────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "VOTRE_WEBHOOK_URL_ICI")
POLL_INTERVAL       = int(os.getenv("POLL_INTERVAL", "15"))
LOG_LEVEL           = os.getenv("LOG_LEVEL", "INFO")

# Mots-clés qui indiquent une alerte importante (breaking)
KEYWORDS_BREAKING = [
    "breaking", "alert", "flash", "urgent", "headline",
    "just in", "sources", "official", "trump", "fed ", "fomc",
    "rate decision", "emergency", "sanctions", "nuclear",
    "ceasefire", "deal", "agreement", "ban", "crash", "surge",
]

# Sources RSS Financial Juice (elles changent parfois, on essaie toutes)
RSS_URLS = [
    "https://www.financialjuice.com/feed",
    "https://www.financialjuice.com/rss",
    "https://www.financialjuice.com/feed/rss",
]

# ─────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fj-bot")

seen_ids: set[str] = set()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ──────────────────────────────────────────────────────────────
#  SCRAPING via RSS
# ──────────────────────────────────────────────────────────────
async def fetch_rss(session: aiohttp.ClientSession) -> list[dict]:
    for url in RSS_URLS:
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.debug(f"RSS {url} → HTTP {resp.status}")
                    continue
                text = await resp.text()
                items = parse_rss(text)
                if items:
                    log.debug(f"RSS OK depuis {url} : {len(items)} items")
                    return items
        except Exception as e:
            log.debug(f"RSS {url} erreur : {e}")
            continue

    # Fallback : scraping direct de la page avec une regex sur le HTML brut
    return await fetch_html_fallback(session)


def parse_rss(xml_text: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}

        for item in root.iter("item"):
            title_el = item.find("title")
            desc_el   = item.find("description")
            guid_el   = item.find("guid")
            date_el   = item.find("pubDate")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            desc  = desc_el.text.strip()  if desc_el  is not None and desc_el.text  else ""
            text  = title or desc
            if not text:
                continue

            uid = guid_el.text.strip() if guid_el is not None and guid_el.text else md5(text.encode()).hexdigest()
            pub = date_el.text.strip()  if date_el is not None and date_el.text  else ""

            items.append({"id": uid, "text": text, "tags": [], "time": pub})
    except ET.ParseError as e:
        log.warning(f"XML parse error : {e}")
    return items


async def fetch_html_fallback(session: aiohttp.ClientSession) -> list[dict]:
    """Scrape la page HTML et extrait les textes qui ressemblent à des headlines."""
    try:
        async with session.get(
            "https://www.financialjuice.com/home",
            headers={**HEADERS, "Accept": "text/html"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()

        # Cherche des blocs de texte entre balises qui ressemblent à des news
        # FJ stocke souvent les headlines dans des attributs data-* ou du JSON embarqué
        items = []

        # Pattern 1 : JSON embarqué dans la page
        json_blocks = re.findall(r'\{[^{}]*"headline"[^{}]*\}', html)
        for block in json_blocks[:30]:
            m = re.search(r'"headline"\s*:\s*"([^"]{20,200})"', block)
            if m:
                text = m.group(1)
                uid  = md5(text.encode()).hexdigest()
                items.append({"id": uid, "text": text, "tags": [], "time": ""})

        # Pattern 2 : textes entre balises <h\d> ou <p> contenant des mots-clés
        if not items:
            texts = re.findall(r'<(?:h\d|p|span)[^>]*>([^<]{30,200})</(?:h\d|p|span)>', html)
            for text in texts:
                text = re.sub(r'\s+', ' ', text).strip()
                lower = text.lower()
                if any(kw in lower for kw in KEYWORDS_BREAKING):
                    uid = md5(text.encode()).hexdigest()
                    items.append({"id": uid, "text": text, "tags": [], "time": ""})

        log.debug(f"HTML fallback : {len(items)} items trouvés")
        return items

    except Exception as e:
        log.error(f"HTML fallback erreur : {e}")
        return []


def is_breaking(headline: dict) -> bool:
    """Retourne True si le headline semble être une alerte importante."""
    text_lower = headline["text"].lower()
    return any(kw in text_lower for kw in KEYWORDS_BREAKING)


# ──────────────────────────────────────────────────────────────
#  DISCORD
# ──────────────────────────────────────────────────────────────
async def send_discord_alert(session: aiohttp.ClientSession, headline: dict):
    tags_str = "  •  ".join(headline["tags"]) if headline["tags"] else ""
    time_str = headline["time"] or datetime.now(timezone.utc).strftime("%H:%M UTC")

    embed = {
        "title": "🚨 BREAKING — Financial Juice",
        "description": f"**{headline['text']}**",
        "color": 0xE8002D,
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
        log.info("Chargement initial des headlines (pas d'envoi)…")
        initial = await fetch_rss(session)
        for h in initial:
            seen_ids.add(h["id"])
        log.info(f"{len(seen_ids)} headlines existants ignorés.")

        if len(seen_ids) == 0:
            log.warning("⚠️  Aucun headline récupéré au démarrage — le RSS est peut-être inaccessible.")
            log.warning("Le bot continue et tentera à chaque cycle.")

        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                headlines = await fetch_rss(session)
                new_ones = [h for h in headlines if h["id"] not in seen_ids]

                if new_ones:
                    log.info(f"{len(new_ones)} nouveau(x) headline(s)")
                    for h in new_ones:
                        seen_ids.add(h["id"])
                        if is_breaking(h):
                            await send_discord_alert(session, h)
                            await asyncio.sleep(0.5)
                        else:
                            log.debug(f"Ignoré (pas breaking) : {h['text'][:60]}")
                else:
                    log.debug("Pas de nouveau headline.")
            except Exception as e:
                log.error(f"Erreur dans la boucle principale : {e}")

if __name__ == "__main__":
    asyncio.run(main())
