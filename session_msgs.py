import json
import os
from logger import log

TRACK_FILE = "session_messages.json"


def _load() -> list:
    try:
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rows: list):
    try:
        with open(TRACK_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f)
    except Exception as e:
        log(f"Hiba a {TRACK_FILE} mentésekor: {e}", "warn")


def track(msg):
    """Munkamenet üzenetek (DM) megjegyzése a bot leállításakor/újraindításakor történő törléshez."""
    if msg is None:
        return msg
    rows = _load()
    key = [msg.channel.id, msg.id]
    if key not in rows:
        rows.append(key)
        _save(rows)
    return msg


async def purge_tracked(bot) -> int:
    """Törli az összes megjegyzett munkamenet üzenetet. Visszaadja a törölt üzenetek számát."""
    rows = _load()
    _save([])
    deleted = 0
    for channel_id, message_id in rows:
        try:
            ch = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            msg = ch.get_partial_message(message_id)
            await msg.delete()
            deleted += 1
        except Exception:
            pass
    if rows:
        log(f"{deleted}/{len(rows)} korábbi munkamenet üzenet törölve a DM-ekből", "ok")
    return deleted
