import json
import time
import random
import traceback
import asyncio
from datetime import datetime
from logger import log, Colors
from config import AUTO_ACCEPT, POLL_INTERVAL, HEARTBEAT_INTERVAL, DEBUG
import config
from discord_client import DiscordAPI
from quest_utils import (
    _get,
    get_quest_name,
    get_task_type,
    is_enrolled,
    is_completed,
    is_completable,
    get_seconds_needed,
    get_seconds_done,
    get_enrolled_at
)

def get_progress_bar(done: float, needed: float) -> str:
    """Vizuális emoji folyamatjelző sáv generálása."""
    if needed <= 0:
        return "░░░░░░░░░░ 0%"
    ratio = min(1.0, max(0.0, done / needed))
    filled = int(ratio * 10)
    empty = 10 - filled
    return "▓" * filled + "░" * empty + f" {ratio * 100:.0f}%"

class QuestAutocompleter:
    def __init__(self, api: DiscordAPI, user_id: int, status_callback=None):
        self.api = api
        self.user_id = user_id
        self.completed_ids = set()
        self.status_callback = status_callback
        self.stop_event = asyncio.Event()
        self.current_quest_id = None
        self.failed_404_ids = set()
        self.last_quests = []
        self.current_quest_info = None

    async def stop(self):
        self.stop_event.set()
        await self.api.close()

    async def claim_reward(self, quest_id: str, platform: int = 0) -> dict:
        """Jutalom beváltási kérelem küldése a befejezett küldetéshez."""
        try:
            r = await self.api.post(f"/quests/{quest_id}/claim-reward", {
                "platform": platform,
                "location": 11,
                "is_targeted": False,
                "metadata_raw": None,
                "metadata_sealed": None,
                "traffic_metadata_raw": None,
                "traffic_metadata_sealed": None
            })
            if r.status == 200:
                return await r.json()
            else:
                body = await r.text()
                log(f"Jutalom átvételi hiba: státusz {r.status}, válasz: {body[:200]}", "error")
                try:
                    err_json = json.loads(body)
                    if "captcha_key" in err_json or "captcha-required" in body or "captcha_service" in err_json:
                        return {
                            "error": "captcha_required",
                            "captcha": True,
                            "captcha_data": err_json,
                            "details": body
                        }
                except Exception:
                    if "captcha-required" in body:
                        return {
                            "error": "captcha_required",
                            "captcha": True,
                            "details": body
                        }
                return {"error": f"Discord API státusz: {r.status}", "details": body}
        except Exception as e:
            log(f"Kivétel jutalom átvételekor: {e}", "error")
            return {"error": str(e)}

    async def fetch_quests(self) -> list:
        try:
            r = await self.api.get("/quests/@me")

            if r.status == 200:
                data = await r.json()
                if isinstance(data, dict):
                    quests = data.get("quests", [])
                    excluded = data.get("excluded_quests", [])
                    blocked = _get(data, "quest_enrollment_blocked_until")
                    if blocked:
                        log(f"Küldetés felvétel blokkolva eddig: {blocked}", "warn")
                    if excluded:
                        log(f"{len(excluded)} küldetés kizárva", "debug")
                    self.last_quests = quests
                    return quests
                elif isinstance(data, list):
                    self.last_quests = data
                    return data
                return []

            elif r.status == 429:
                retry_after = (await r.json()).get("retry_after", 10)
                log(f"Rate limited – várakozás: {retry_after} mp", "warn")
                await asyncio.sleep(retry_after)
                return await self.fetch_quests()
            else:
                log(f"Küldetés lekérési hiba ({r.status}): {(await r.text())[:200]}", "warn")
                return []

        except Exception as e:
            log(f"Hiba a küldetések lekérésekor: {e}", "error")
            if DEBUG:
                traceback.print_exc()
            return []

    async def enroll_quest(self, quest: dict) -> bool:
        name = get_quest_name(quest)
        qid = quest["id"]

        for attempt in range(1, 4):
            try:
                r = await self.api.post(f"/quests/{qid}/enroll", {
                    "location": 11,
                    "is_targeted": False,
                    "metadata_raw": None,
                    "metadata_sealed": None,
                    "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
                    "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
                })

                if r.status == 429:
                    retry_after = (await r.json()).get("retry_after", 5)
                    wait = retry_after + 1
                    log(f"Rate limited \"{name}\" felvételekor ({attempt}/3) – várakozás: {wait} mp", "warn")
                    await asyncio.sleep(wait)
                    continue

                if r.status == 404:
                    log(f"A(z) \"{name}\" küldetés nem elérhető (404), kihagyás.", "debug")
                    self.failed_404_ids.add(qid)
                    return False

                if r.status in (200, 201, 204):
                    log(f"Elfogadva: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                    return True

                log(f"\"{name}\" felvétele sikertelen ({r.status}): {(await r.text())[:200]}", "warn")
                return False

            except Exception as e:
                log(f"Hiba a(z) \"{name}\" felvételekor: {e}", "error")
                return False

        log(f"\"{name}\" kihagyva 3 sikertelen rate limit kísérlet után", "warn")
        return False

    async def auto_accept(self, quests: list) -> list:
        if not AUTO_ACCEPT:
            return quests

        unaccepted = [
            q for q in quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]

        if not unaccepted:
            return quests

        log(f"{len(unaccepted):02d} új küldetés találva – automatikus elfogadás...", "info")
        if self.status_callback:
            await self.status_callback(f"{config.EMOJI_LOADING} **{len(unaccepted):02d} új küldetés felvétele...**")

        for q in unaccepted:
            if self.stop_event.is_set():
                break
            await self.enroll_quest(q)
            await asyncio.sleep(3)

        await asyncio.sleep(2)
        return await self.fetch_quests()

    async def complete_video(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        enrolled_at_str = get_enrolled_at(quest)

        if enrolled_at_str:
            enrolled_ts = datetime.fromisoformat(enrolled_at_str.replace("Z", "+00:00")).timestamp()
        else:
            enrolled_ts = time.time()

        log(f"🎬 Videó: {Colors.BOLD}{name}{Colors.RESET} ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)", "info")
        if self.status_callback:
            await self.status_callback(
                f"🎬 **Küldetés:** {name}\n"
                f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                f"⚡ *Típus:* Videónézés (Mobil/PC)"
            )

        max_future = 10
        speed = 7
        interval = 1

        while seconds_done < seconds_needed:
            if self.stop_event.is_set():
                log(f"Küldetés leállítva: {name}", "info")
                return

            max_allowed = (time.time() - enrolled_ts) + max_future
            diff = max_allowed - seconds_done
            timestamp = seconds_done + speed

            if diff >= speed:
                try:
                    r = await self.api.post(f"/quests/{qid}/video-progress", {
                        "timestamp": min(seconds_needed, timestamp + random.random())
                    })
                    if r.status == 200:
                        body = await r.json()
                        if body.get("completed_at"):
                            log(f"✅ Befejezve: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                            return
                        seconds_done = min(seconds_needed, timestamp)
                        if self.current_quest_info:
                            self.current_quest_info["done"] = seconds_done
                        log(f"  [{name}] {int(seconds_done):02d}/{int(seconds_needed):02d} mp", "progress")
                        if self.status_callback:
                            await self.status_callback(
                                f"🎬 **Küldetés:** {name}\n"
                                f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                                f"⚡ *Típus:* Videónézés (Mobil/PC)"
                            )
                    elif r.status == 429:
                        retry_after = (await r.json()).get("retry_after", 5)
                        log(f"  Rate limited – várakozás: {retry_after + 1} mp", "warn")
                        await asyncio.sleep(retry_after + 1)
                        continue
                    else:
                        log(f"  Videó haladási hiba ({r.status}): {(await r.text())[:200]}", "warn")
                except Exception as e:
                    log(f"  Hiba: {e}", "error")

            if timestamp >= seconds_needed:
                break
            await asyncio.sleep(interval)

        try:
            await self.api.post(f"/quests/{qid}/video-progress", {"timestamp": seconds_needed})
        except Exception:
            pass
        log(f"✅ Befejezve: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    async def complete_heartbeat(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        task_type = get_task_type(quest)
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        log(
            f"🎮 {task_type}: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60:02d} perc van hátra)",
            "info"
        )
        if self.status_callback:
            await self.status_callback(
                f"🎮 **Küldetés:** {name}\n"
                f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                f"⚡ *Típus:* {task_type}\n"
                f"⏳ ~{remaining // 60:02d} perc van hátra"
            )

        pid = random.randint(1000, 30000)

        while seconds_done < seconds_needed:
            if self.stop_event.is_set():
                log(f"Küldetés leállítva: {name}", "info")
                return

            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": f"call:0:{pid}",
                    "terminal": False,
                })

                if r.status == 200:
                    body = await r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and task_type in progress_data:
                        seconds_done = progress_data[task_type].get("value", seconds_done)
                    if self.current_quest_info:
                        self.current_quest_info["done"] = seconds_done
                    log(f"  [{name}] {int(seconds_done):02d}/{int(seconds_needed):02d} mp", "progress")

                    remaining = max(0, seconds_needed - seconds_done)
                    if self.status_callback:
                        await self.status_callback(
                            f"🎮 **Küldetés:** {name}\n"
                            f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                            f"⚡ *Típus:* {task_type}\n"
                            f"⏳ ~{remaining // 60:02d} perc van hátra"
                        )

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        log(f"✅ Befejezve: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                        return

                elif r.status == 429:
                    retry_after = (await r.json()).get("retry_after", 10)
                    log(f"  Rate limited – várakozás: {retry_after + 1} mp", "warn")
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log(f"  Heartbeat hiba ({r.status}): {await r.text()[:200]}", "warn")

            except Exception as e:
                log(f"  Heartbeat hiba: {e}", "error")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": f"call:0:{pid}",
                "terminal": True,
            })
        except Exception:
            pass
        log(f"✅ Befejezve: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    async def complete_activity(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        log(
            f"🕹️  Aktivitás: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60:02d} perc van hátra)",
            "info"
        )
        if self.status_callback:
            await self.status_callback(
                f"🕹️ **Küldetés:** {name}\n"
                f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                f"⚡ *Típus:* Aktivitás játék\n"
                f"⏳ ~{remaining // 60:02d} perc van hátra"
            )

        stream_key = "call:0:1"

        while seconds_done < seconds_needed:
            if self.stop_event.is_set():
                log(f"Küldetés leállítva: {name}", "info")
                return

            try:
                r = await self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": stream_key,
                    "terminal": False,
                })

                if r.status == 200:
                    body = await r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and "PLAY_ACTIVITY" in progress_data:
                        seconds_done = progress_data["PLAY_ACTIVITY"].get("value", seconds_done)
                    if self.current_quest_info:
                        self.current_quest_info["done"] = seconds_done
                    log(f"  [{name}] {int(seconds_done):02d}/{int(seconds_needed):02d} mp", "progress")

                    remaining = max(0, seconds_needed - seconds_done)
                    if self.status_callback:
                        await self.status_callback(
                            f"🕹️ **Küldetés:** {name}\n"
                            f"📈 **Haladás:** `{get_progress_bar(seconds_done, seconds_needed)}` ({int(seconds_done):02d}/{int(seconds_needed):02d} mp)\n"
                            f"⚡ *Típus:* Aktivitás játék\n"
                            f"⏳ ~{remaining // 60:02d} perc van hátra"
                        )

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        break
                elif r.status == 429:
                    retry_after = (await r.json()).get("retry_after", 10)
                    log(f"  Rate limited – várakozás: {retry_after + 1} mp", "warn")
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log(f"  Heartbeat hiba ({r.status}): {(await r.text())[:200]}", "warn")
            except Exception as e:
                log(f"  Hiba: {e}", "error")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        try:
            await self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": stream_key,
                "terminal": True,
            })
        except Exception:
            pass
        log(f"✅ Befejezve: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    async def process_quest(self, quest: dict):
        qid = quest.get("id")
        name = get_quest_name(quest)
        task_type = get_task_type(quest)

        if not task_type:
            log(f"\"{name}\" – nem támogatott feladattípus, kihagyva", "warn")
            return

        if qid in self.completed_ids:
            return

        log(f"━━━ Indítás: {Colors.BOLD}{name}{Colors.RESET} (típus: {task_type}) ━━━", "info")

        self.current_quest_id = qid
        self.current_quest_info = {
            "name": name,
            "task_type": task_type,
            "needed": get_seconds_needed(quest),
            "done": get_seconds_done(quest),
            "quest": quest
        }
        try:
            if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                await self.complete_video(quest)
            elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
                await self.complete_heartbeat(quest)
            elif task_type == "PLAY_ACTIVITY":
                await self.complete_activity(quest)

            if not self.stop_event.is_set():
                from limits_manager import increment_user_total_completed, save_global_stats
                increment_user_total_completed(self.user_id)
                self.completed_ids.add(qid)
                import config
                config.TOTAL_COMPLETED += 1
                save_global_stats()
                try:
                    from limits_manager import increment_guild_completed
                    increment_guild_completed((config.ACTIVE_USERS.get(self.user_id) or {}).get("guild_id"))
                except Exception:
                    pass
                if self.status_callback:
                    await self.status_callback(f"{config.EMOJI_SUCCESS} **Befejezve:** {name} 🎉", update_panel=True)
        except Exception as e:
            log(f"Hiba a(z) {name} küldetés feldolgozásakor: {e}", "error")
            import config
            config.TOTAL_FAILED += 1
            from limits_manager import save_global_stats
            save_global_stats()
            if self.status_callback:
                await self.status_callback(f"{config.EMOJI_FAIL} **Sikertelen:** {name} (Hiba: {str(e)})", update_panel=True)
        finally:
            self.current_quest_id = None
            self.current_quest_info = None

    async def run(self):
        log("=" * 60, "info")
        log(f"{Colors.BOLD}Discord Quest Auto-Completer v4.0{Colors.RESET}", "info")
        log(f"Auto-accept: {'BE' if AUTO_ACCEPT else 'KI'}  |  Lekérdezés: {POLL_INTERVAL}s", "info")
        log("=" * 60, "info")

        if self.status_callback:
            await self.status_callback("⚡ **Küldetések ellenőrzése folyamatban...**")

        # Felhasználói limit ellenőrzése indítás előtt
        from limits_manager import is_user_limited, consume_limit
        if is_user_limited(self.user_id):
            log(f"A(z) {self.user_id} azonosítójú felhasználó elérte a limitet. Leállítás...", "warn")
            if self.status_callback:
                await self.status_callback("LIMIT_REACHED", finished=True)
            return

        # 1 használat levonása erre a menetre
        consume_limit(self.user_id)

        cycle = 0
        while not self.stop_event.is_set():
            cycle += 1
            log(f"── {cycle:02d}. ellenőrzési ciklus ──", "info")

            quests = await self.fetch_quests()
            total = len(quests)

            if not quests:
                log("Nincs küldetés a fiókban", "info")
                if self.status_callback:
                    await self.status_callback("📭 **Nem található Quest a Discord fiókodban.**", finished=True)
                break
            else:
                enrolled_count = sum(1 for q in quests if is_enrolled(q))
                completed_count = sum(1 for q in quests if is_completed(q))
                completable_count = sum(1 for q in quests if is_completable(q))

                log(
                    f"Összesen: {total:02d} küldetés | Elfogadva: {enrolled_count:02d} | "
                    f"Kész: {completed_count:02d} | Teljesíthető: {completable_count:02d}",
                    "info"
                )

                quests = await self.auto_accept(quests)

                actionable = [
                    q for q in quests
                    if is_enrolled(q) and not is_completed(q) and is_completable(q)
                    and q.get("id") not in self.completed_ids
                ]

                if actionable:
                    log(f"\n{len(actionable):02d} elvégzendő küldetés:", "info")
                    for q in actionable:
                        if self.stop_event.is_set():
                            break
                        await self.process_quest(q)
                else:
                    log("Jelenleg nincs elvégzendő küldetés", "info")
                    if quests and all(is_completed(q) for q in quests):
                        log("🎉 Minden küldetés 100%-ban befejezve! Munkamenet lezárva.", "ok")
                        if self.status_callback:
                            await self.status_callback("FINISHED", finished=True)
                        break
                    else:
                        if self.status_callback:
                            quest_list_str = []
                            for q in quests:
                                name = get_quest_name(q)
                                task = get_task_type(q) or "?"
                                if is_completed(q):
                                    status_emoji = config.EMOJI_SUCCESS
                                elif is_enrolled(q):
                                    status_emoji = config.EMOJI_LOADING
                                else:
                                    status_emoji = "⚪"
                                quest_list_str.append(f"{status_emoji} **{name}** [{task}]")

                            await self.status_callback(
                                "💤 **Jelenleg nincs új elvégzendő feladat.**\n"
                                "**A küldetéseid állapota:**\n" + "\n".join(quest_list_str[:10]),
                                finished=True
                            )
                        break

            # Várakozás a következő ciklusra
            log(f"\nVárakozás {POLL_INTERVAL}s...\n", "info")
            for _ in range(POLL_INTERVAL):
                if self.stop_event.is_set():
                    break
                await asyncio.sleep(1)
