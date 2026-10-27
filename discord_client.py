import base64
import json
import re
import aiohttp
from typing import Optional
from logger import log, Colors
from config import API_BASE

async def fetch_latest_build_number() -> int:
    """Scrape Discord web app to get the latest client_build_number."""
    FALLBACK = 504649
    try:
        log("Legújabb Discord build szám lekérése...", "info")
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15) as r:
                if r.status != 200:
                    log(f"Nem sikerült betölteni a Discord oldalt ({r.status}), tartalék használata", "warn")
                    return FALLBACK
                text = await r.text()

        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', text)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', text)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]

        if not scripts:
            log("JS assetek nem találhatók, tartalék használata", "warn")
            return FALLBACK

        async with aiohttp.ClientSession() as session:
            for asset_hash in scripts[-5:]:
                try:
                    async with session.get(
                        f"https://discord.com/assets/{asset_hash}.js",
                        headers={"User-Agent": ua}, timeout=15
                    ) as ar:
                        if ar.status == 200:
                            js_text = await ar.text()
                            m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', js_text)
                            if m:
                                bn = int(m.group(1))
                                log(f"Build number: {Colors.BOLD}{bn}{Colors.RESET}", "ok")
                                return bn
                except Exception:
                    continue

        log(f"Build szám nem található, tartalék használata: {FALLBACK}", "warn")
        return FALLBACK
    except Exception as e:
        log(f"Hiba a build szám lekérésekor: {e}, tartalék használata: {FALLBACK}", "warn")
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    """Create base64-encoded X-Super-Properties header."""
    obj = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.26100",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "browser_version": "32.2.7",
        "client_build_number": build_number,
        "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


class DiscordAPI:
    def __init__(self, token: str, build_number: int):
        self.token = token
        self.build_number = build_number
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        )
        sp = make_super_properties(build_number)
        self.headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ua,
            "X-Super-Properties": sp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        }
        self.session = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        url = f"{API_BASE}{path}"
        log(f"GET {path}", "debug")
        session = await self.get_session()
        r = await session.get(url, **kwargs)
        log(f"  -> {r.status} (status)", "debug")
        return r

    async def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> aiohttp.ClientResponse:
        url = f"{API_BASE}{path}"
        log(f"POST {path}", "debug")
        session = await self.get_session()
        r = await session.post(url, json=payload, **kwargs)
        log(f"  -> {r.status} (status)", "debug")
        return r

    async def validate_token(self) -> Optional[dict]:
        try:
            r = await self.get("/users/@me")
            if r.status == 200:
                user = await r.json()
                name = user.get("username", "?")
                log(f"Bejelentkezve: {Colors.BOLD}{name}{Colors.RESET} (ID: {user['id']})", "ok")
                return user
            else:
                log(f"Érvénytelen token (státusz {r.status})", "error")
                return None
        except Exception as e:
            log(f"Nem sikerült csatlakozni a Discordhoz: {e}", "error")
            return None
