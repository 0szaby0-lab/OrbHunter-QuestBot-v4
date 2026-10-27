"""Animált emojik (GIF) szinkronizálása az emojis/ mappából a bot Application Emojis-ba."""
import os

import config
from logger import log

EMOJI_DIR = "emojis"
# gif fájlnév -> config attribútum; csak akkor írja felül, ha a .env-ben nincs egyéni emoji
EMOJI_MAP = {
    "loading": "EMOJI_LOADING",
    "success": "EMOJI_SUCCESS",
    "fail": "EMOJI_FAIL",
    "rocket": "EMOJI_ROCKET",
}


async def sync_application_emojis(bot):
    """Hiányzó GIF-ek feltöltése a botra és alapértelmezett emojiként használata."""
    try:
        existing = {e.name: e for e in await bot.fetch_application_emojis()}
        for name, attr in EMOJI_MAP.items():
            if os.getenv(attr):
                continue  # manuálisan konfigurálva a .env fájlban
            emoji = existing.get(name)
            if emoji is None:
                path = os.path.join(EMOJI_DIR, f"{name}.gif")
                if not os.path.exists(path):
                    continue
                with open(path, "rb") as f:
                    emoji = await bot.create_application_emoji(name=name, image=f.read())
                log(f"'{name}' animált emoji feltöltve a botra", "ok")
            setattr(config, attr, str(emoji))
    except Exception as e:
        log(f"Nem sikerült szinkronizálni az animált emojikat: {e}", "warn")
