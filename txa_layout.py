"""txa_layout — Discord Components V2 felhasználói felület a Quest Bothoz.
100% Magyarosított felület és navigáció.
"""
import asyncio
import inspect
import time

import discord

import config
from limits_manager import (
    get_price_per_quest,
    set_price_per_quest,
    get_transaction_timeout,
    set_transaction_timeout,
)

MISSING = discord.utils.MISSING
ACCENT = discord.Colour.from_rgb(88, 101, 242)
GREEN = discord.Colour.from_rgb(87, 242, 135)
YELLOW = discord.Colour.from_rgb(254, 231, 92)
ORANGE = discord.Colour.from_rgb(250, 166, 26)
RED = discord.Colour.from_rgb(237, 66, 69)

PACKAGE_EMOJI = {1: "💎", 2: "🎫", 5: "🔥", 10: "👑", 20: "🚀", 50: "🏆"}


# ───────────────────────── Közös UI Építőelemek ─────────────────────────

BOT_AVATAR = None
INVITE_URL = None
QUEST_HOME_URL = "https://discord.com/quest-home"


def configure(avatar: str = None, invite: str = None):
    global BOT_AVATAR, INVITE_URL
    if avatar:
        BOT_AVATAR = avatar
    if invite:
        INVITE_URL = invite


def link_buttons(*which) -> list:
    out = []
    for w in which:
        if w == "invite" and INVITE_URL:
            out.append(discord.ui.Button(label="Bot Meghívása", emoji="➕", style=discord.ButtonStyle.link, url=INVITE_URL))
        elif w == "quest":
            out.append(discord.ui.Button(label="Quest Oldal", emoji="🎁", style=discord.ButtonStyle.link, url=QUEST_HOME_URL))
        elif w == "discord":
            out.append(discord.ui.Button(label="Discord Web", emoji="💬", style=discord.ButtonStyle.link, url="https://discord.com/app"))
    return out


def _with_hero(items: list) -> list:
    if not BOT_AVATAR or not items or not isinstance(items[0], str) or not items[0].startswith("### "):
        return items
    items = list(items)
    texts = [items[0]]
    for j in range(1, len(items)):
        it = items[j]
        if it is None:
            continue
        if isinstance(it, str) and it != "---" and not it.startswith("```") and len(it) < 600:
            texts.append(it)
            del items[j]
        break
    items[0] = discord.ui.Section(*texts, accessory=discord.ui.Thumbnail(BOT_AVATAR))
    return items


def box(*items, accent=ACCENT, hero: bool = True) -> discord.ui.Container:
    c = discord.ui.Container(accent_colour=accent)
    for it in (_with_hero(list(items)) if hero else items):
        if it is None:
            continue
        if isinstance(it, str):
            c.add_item(discord.ui.Separator() if it == "---" else discord.ui.TextDisplay(it))
        else:
            c.add_item(it)
    return c


def row_of(*items) -> discord.ui.ActionRow:
    r = discord.ui.ActionRow()
    for i in items:
        r.add_item(i)
    return r


def btn(label, emoji, callback, style=discord.ButtonStyle.secondary, disabled=False) -> discord.ui.Button:
    b = discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled)
    b.callback = callback
    return b


async def _resolve(back, interaction):
    result = back(interaction)
    if inspect.isawaitable(result):
        result = await result
    return result[1] if isinstance(result, tuple) else result


class TxaView(discord.ui.LayoutView):
    admin_only = False

    def __init__(self, user_id: int, back=None, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.back = back

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.admin_only:
            if interaction.user.id not in config.ADMIN_IDS:
                await interaction.response.send_message(f"{config.EMOJI_FAIL} Csak Adminisztrátorok használhatják ezt a felületet.", ephemeral=True)
                return False
            return True
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a felület nem a te fiókodhoz tartozik.", ephemeral=True)
            return False
        return True

    def on_leave(self):
        pass

    async def go_back(self, interaction: discord.Interaction):
        self.on_leave()
        view = await _resolve(self.back, interaction)
        await interaction.response.edit_message(view=view)

    async def go(self, interaction: discord.Interaction, view: "TxaView"):
        self.on_leave()
        await interaction.response.edit_message(view=view)

    def nav_row(self, refresh=None, help_text: str = None, extra=()) -> discord.ui.ActionRow:
        items = []
        if self.back:
            items.append(btn("Vissza", "◀️", self._on_back_click))
        if refresh:
            items.append(btn("Frissítés", "🔄", refresh))
        for b in extra:
            items.append(b)
        if help_text:
            async def show_help(interaction: discord.Interaction, text=help_text):
                await interaction.response.send_message(text, ephemeral=True)
            items.append(btn("Segítség", "❓", show_help))
        return row_of(*items)

    async def _on_back_click(self, interaction: discord.Interaction):
        await self.go_back(interaction)


class SimpleLayoutView(TxaView):
    def __init__(self, user_id, title, body, accent=ACCENT, back=None, refresh=None, thumb=None, footer=None, help_text=None):
        super().__init__(user_id, back)
        self.title, self.body, self.accent = title, body, accent
        self.refresh_cb, self.thumb, self.footer, self.help_text = refresh, thumb, footer, help_text
        self.build()

    def build(self):
        self.clear_items()
        head = f"### {self.title}\n{self.body}"
        if self.thumb:
            top = discord.ui.Section(head, accessory=discord.ui.Thumbnail(self.thumb))
        else:
            top = head
        self.add_item(box(top, f"-# {self.footer}" if self.footer else None, accent=self.accent))

        async def do_refresh(interaction: discord.Interaction):
            fresh = self.refresh_cb()
            if inspect.isawaitable(fresh):
                fresh = await fresh
            self.title, self.body = fresh
            self.build()
            await interaction.response.edit_message(view=self)

        nav = self.nav_row(refresh=do_refresh if self.refresh_cb else None, help_text=self.help_text)
        if nav.children:
            self.add_item(nav)


# ───────────────────────── Beállítások (Config) ─────────────────────────

def _bool_values(on_desc: str, off_desc: str):
    return [("True", "Bekapcsolva ✅", on_desc), ("False", "Kikapcsolva ❌", off_desc)]


def _settings():
    def env_setter(key):
        return lambda v: config.update_env_variable(key, v)

    return {
        "sys": {
            "label": "Rendszer", "emoji": "⚙️",
            "desc": "Lekérdezési időköz, heartbeat és automata funkciók.",
            "items": [
                {
                    "key": "POLL_INTERVAL", "emoji": "🔄", "label": "Küldetés lekérdezési gyakoriság",
                    "desc": "Várakozási idő két küldetés-keresés között fiókonként.",
                    "values": [("15", "15 másodperc", "Nagyon gyors • magas erőforrás-használat"),
                               ("30", "30 másodperc", "Gyors • az ajánlott beállítás"),
                               ("60", "60 másodperc", "Kiegyensúlyozott"),
                               ("120", "120 másodperc", "Lassú • energiatakarékos"),
                               ("300", "300 másodperc", "Nagyon lassú")],
                    "custom": {"min": 5, "max": 3600, "unit": "mp"},
                    "get": lambda: str(config.POLL_INTERVAL), "set": env_setter("POLL_INTERVAL"),
                },
                {
                    "key": "HEARTBEAT_INTERVAL", "emoji": "💓", "label": "Heartbeat gyakoriság",
                    "desc": "A küldetés-haladás számításához küldött szívverés csomagok időköz.",
                    "values": [("10", "10 másodperc", "Sűrű • nagyon sima haladás"),
                               ("15", "15 másodperc", "Ajánlott"),
                               ("20", "20 másodperc", "Discord alapértelmezett"),
                               ("30", "30 másodperc", "Ritka • kevesebb kérés")],
                    "custom": {"min": 5, "max": 300, "unit": "mp"},
                    "get": lambda: str(config.HEARTBEAT_INTERVAL), "set": env_setter("HEARTBEAT_INTERVAL"),
                },
                {
                    "key": "AUTO_ACCEPT", "emoji": "📥", "label": "Automatikus elfogadás",
                    "desc": "A bot automatikusan elfogadja az új küldetéseket, amint észleli őket.",
                    "values": _bool_values("Automatikusan elfogadja az új küldetéseket", "Kézi elfogadás szükséges"),
                    "get": lambda: str(bool(config.AUTO_ACCEPT)), "set": env_setter("AUTO_ACCEPT"),
                },
                {
                    "key": "LOG_PROGRESS", "emoji": "📝", "label": "Heartbeat naplózás",
                    "desc": "Minden egyes szívverés részleteinek kiírása a konzolra.",
                    "values": _bool_values("Részletes szívverés kiírás a konzolra", "Letisztultabb konzol"),
                    "get": lambda: str(bool(config.LOG_PROGRESS)), "set": env_setter("LOG_PROGRESS"),
                },
                {
                    "key": "DEBUG", "emoji": "🐞", "label": "Hibakereső mód (Debug)",
                    "desc": "Részletes hibakeresési naplók engedélyezése a hibák feltárásához.",
                    "values": _bool_values("Részletes hibakereső naplózás", "Normál naplózás"),
                    "get": lambda: str(bool(config.DEBUG)), "set": env_setter("DEBUG"),
                },
            ],
        },
        "pay": {
            "label": "Quest & Fizetés", "emoji": "💰",
            "desc": "Egységárak, lejárati idők és Admin jogosultságok.",
            "items": [
                {
                    "key": "price", "emoji": "💵", "label": "Egységár lehetőségenként",
                    "desc": "Az ár megváltoztatása automatikusan újraszámolja a csomagkedvezményeket.",
                    "values": [("500", "500 Ft", "Kedvező ár"),
                               ("1000", "1,000 Ft", "Normál ár • ajánlott"),
                               ("2000", "2,000 Ft", "Prémium ár"),
                               ("5000", "5,000 Ft", "Kiemelt kategória")],
                    "custom": {"min": 100, "max": 100000, "unit": "Ft", "money": True},
                    "get": lambda: str(get_price_per_quest()), "set": lambda v: set_price_per_quest(int(v)),
                },
                {
                    "key": "timeout", "emoji": "⏳", "label": "Fizetési időkorlát",
                    "desc": "Ennyi idő után a kifizetetlen számla érvényét veszti.",
                    "values": [("3", "03 perc", "Rövid"),
                               ("5", "05 perc", "Alapértelmezett • ajánlott"),
                               ("10", "10 perc", "Kényelmes utalási idő"),
                               ("15", "15 perc", "Hosszú"),
                               ("30", "30 perc", "Nagyon hosszú")],
                    "custom": {"min": 1, "max": 120, "unit": "perc"},
                    "get": lambda: str(get_transaction_timeout() // 60),
                    "set": lambda v: set_transaction_timeout(int(v) * 60),
                },
                {
                    "key": "ADMIN_BYPASS_LIMIT", "emoji": "👑", "label": "Admin korlátlan hozzáférés",
                    "desc": "Az Adminok korlátlanul teljesíthetnek küldetéseket kvótalevonás nélkül.",
                    "values": _bool_values("Az Adminok nem fogyasztanak kvótát", "Az Adminokra is vonatkozik a kvóta"),
                    "get": lambda: str(bool(getattr(config, "ADMIN_BYPASS_LIMIT", True))),
                    "set": env_setter("ADMIN_BYPASS_LIMIT"),
                },
            ],
        },
    }


def _label_of(item, value: str) -> str:
    for v, label, _desc in item["values"]:
        if v == value:
            return label
    spec = item.get("custom")
    if spec and value.isdigit():
        return f"{int(value):,} {spec['unit']}" if spec.get("money") else f"{value} {spec['unit']}"
    return value


CUSTOM_VALUE = "__custom__"


def parse_number(text: str, allow_suffix: bool = True):
    t = text.strip().lower().replace(" ", "")
    mult = 1
    if not allow_suffix:
        pass
    elif t.endswith("k"):
        mult, t = 1000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1000000, t[:-1]
    t = t.replace(",", "").replace(".", "").replace("ft", "")
    if not t.isdigit():
        return None
    return int(t) * mult


class CustomValueModal(discord.ui.Modal):
    def __init__(self, layout, item):
        spec = item["custom"]
        super().__init__(title=f"Érték: {item['label']}"[:45])
        self.layout = layout
        self.item = item
        self.spec = spec
        self.example = "1000 vagy 1k" if spec.get("money") else str(max(spec["min"], min(spec["max"], 10)))
        shown_min = f"{spec['min']:,}" if spec.get("money") else spec["min"]
        shown_max = f"{spec['max']:,}" if spec.get("money") else spec["max"]
        self.value_input = discord.ui.TextInput(
            label=f"{item['label']} ({spec['unit']})"[:45],
            placeholder=f"{shown_min} és {shown_max} {spec['unit']} között (pl. {self.example})"[:100],
            default=item["get"](),
            min_length=1,
            max_length=12,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        number = parse_number(self.value_input.value, allow_suffix=bool(self.spec.get("money")))
        lo, hi, unit = self.spec["min"], self.spec["max"], self.spec["unit"]
        if number is None or not (lo <= number <= hi):
            await interaction.response.send_message(
                f"{config.EMOJI_FAIL} Érvénytelen érték! Adj meg egy számot **{lo:,}** és **{hi:,}** {unit} között (példa: `{self.example}`).",
                ephemeral=True
            )
            return
        self.item["set"](str(number))
        self.layout.note = f"{config.EMOJI_SUCCESS} Beállítva: **{self.item['label']}** = **{_label_of(self.item, str(number))}** (egyéni)"
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class GroupSelect(discord.ui.Select):
    def __init__(self, layout, groups):
        options = [
            discord.SelectOption(label=g["label"], value=k, emoji=g["emoji"], description=g["desc"][:100])
            for k, g in groups.items()
        ]
        super().__init__(placeholder="📂 Válassz egy beállítási csoportot", options=options)
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        self.layout.page = self.values[0]
        self.layout.note = None
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class SettingSelect(discord.ui.Select):
    def __init__(self, layout, item):
        current = item["get"]()
        options = []
        known = {v for v, _, _ in item["values"]}
        if current not in known:
            options.append(discord.SelectOption(label=f"{current} (jelenlegi)", value=current, description="Aktuális érték (egyéni)", default=True))
        for v, label, desc in item["values"]:
            options.append(discord.SelectOption(label=label, value=v, description=desc[:100], default=(v == current)))
        spec = item.get("custom")
        if spec:
            if spec.get("money"):
                hint = f"Egyéni összeg: {spec['min']:,}–{spec['max']:,} (pl. 1000 vagy 1k)"
            else:
                hint = f"Egyéni érték: {spec['min']}–{spec['max']} {spec['unit']}"
            options.append(discord.SelectOption(label="Egyéni érték megadása...", value=CUSTOM_VALUE, emoji="✏️", description=hint[:100]))
        super().__init__(placeholder=f"{item['emoji']} Válassz új értéket", options=options[:25])
        self.layout = layout
        self.item = item

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == CUSTOM_VALUE:
            await interaction.response.send_modal(CustomValueModal(self.layout, self.item))
            return
        self.item["set"](value)
        self.layout.note = f"{config.EMOJI_SUCCESS} Beállítva: **{self.item['label']}** = **{_label_of(self.item, value)}**"
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class ConfigLayoutView(TxaView):
    admin_only = True

    def __init__(self, user_id: int, back=None):
        super().__init__(user_id, back)
        self.page = "home"
        self.note = None
        self.build()

    def _summary(self) -> str:
        on = lambda v: "Bekapcsolva ✅" if v else "Kikapcsolva ❌"
        timeout = get_transaction_timeout()
        mapped = getattr(config, "SERVER_CHANNELS", None) or {}
        if mapped:
            shown = " ".join(f"<#{c}>" for c in list(mapped.values())[:4])
            channel = f"{len(mapped)} szerver — {shown}" + (" …" if len(mapped) > 4 else "")
        else:
            channel = f"<#{config.CHANNEL_ID}>" if config.CHANNEL_ID else "Nincs beállítva"
        return (
            f"📺 Működési csatorna: {channel}\n"
            f"👑 Admin azonosítók: `{', '.join(map(str, config.ADMIN_IDS))}`\n"
            f"🔑 Fizetési API: {'Konfigurálva ✅' if config.SEPAY_API_KEY else 'Nincs beállítva ❌'}\n\n"
            f"**⚙️ Rendszer**\n"
            f"🔄 Lekérdezés: `{config.POLL_INTERVAL:02d}s` • 💓 Heartbeat: `{config.HEARTBEAT_INTERVAL:02d}s`\n"
            f"📥 Automata elfogadás: {on(config.AUTO_ACCEPT)} • 📝 Naplózás: {on(config.LOG_PROGRESS)} • 🐞 Debug: {on(config.DEBUG)}\n\n"
            f"**💰 Quest & Fizetés**\n"
            f"💵 Egységár: `{get_price_per_quest():,} Ft`/lehetőség • ⏳ Időkorlát: `{timeout // 60:02d} perc`\n"
            f"👑 Admin végtelen hozzáférés: {on(getattr(config, 'ADMIN_BYPASS_LIMIT', True))}"
        )

    def build(self):
        self.clear_items()
        groups = _settings()
        items = []
        if self.page == "home" or self.page not in groups:
            self.page = "home"
            items += [f"### ⚙️ Rendszerbeállítások {config.EMOJI_ROCKET}", self.note, self._summary(), "---", "**Válassz beállítási kategóriát**"]
            row = discord.ui.ActionRow()
            row.add_item(GroupSelect(self, groups))
            items += [row, "-# Válassz csoportot, majd módosítsd a kívánt értékeket. A módosítások azonnal érvénybe lépnek."]
        else:
            group = groups[self.page]
            items += [f"### {group['emoji']} {group['label']}", group["desc"], self.note]
            for item in group["items"]:
                row = discord.ui.ActionRow()
                row.add_item(SettingSelect(self, item))
                items += ["---", f"**{item['emoji']} {item['label']}**", row, f"-# {item['desc']}"]
        self.add_item(box(*items))

        back_ok = self.page != "home" or self.back is not None
        self.add_item(row_of(
            btn("Vissza", "◀️", self._on_back, disabled=not back_ok),
            btn("Frissítés", "🔄", self._on_refresh),
            btn("Segítség", "❓", self._on_help),
        ))

    async def _on_back(self, interaction: discord.Interaction):
        if self.page != "home":
            self.page, self.note = "home", None
            self.build()
            await interaction.response.edit_message(view=self)
        else:
            await self.go_back(interaction)

    async def _on_refresh(self, interaction: discord.Interaction):
        self.note = None
        self.build()
        await interaction.response.edit_message(view=self)

    async def _on_help(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "ℹ️ **Használati útmutató:** Válassz egy kategóriát, majd a legördülő menüben válassz új értéket. "
            "A módosítások azonnal életbe lépnek és mentésre kerülnek. "
            "A **Vissza** gombbal térhetsz vissza az előző menübe.",
            ephemeral=True
        )


# ───────────────────────── Rendszerstátusz (Status) ─────────────────────────

class StopSessionSelect(discord.ui.Select):
    def __init__(self, layout, bot):
        options = []
        for uid, info in list(config.ACTIVE_USERS.items())[:25]:
            completer = info.get("completer")
            curr = getattr(completer, "current_quest_info", None) if completer else None
            if curr:
                needed = curr.get("needed", 1)
                pct = int(curr.get("done", 0) / needed * 100) if needed else 0
                desc = f"Folyamatban: {str(curr.get('name', 'Quest'))[:30]} ({pct}%)"
            else:
                desc = "Küldetések keresése folyamatban"
            options.append(discord.SelectOption(
                label=f"{str(info.get('username', 'Ismeretlen'))[:40]} ({uid})"[:100],
                value=str(uid), description=desc[:100], emoji="🛑"
            ))
        super().__init__(placeholder="🛑 Válassz egy munkamenetet a leállításhoz", options=options)
        self.layout = layout
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        uid = int(self.values[0])
        info = config.ACTIVE_USERS.pop(uid, None)
        if info:
            try:
                await info["completer"].stop()
                if info.get("task"):
                    info["task"].cancel()
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                if user:
                    await user.send("🛑 **Az aktív Quest munkamenetedet egy adminisztrátor leállította.**")
            except Exception:
                pass
            try:
                await self.bot.update_control_panel()
            except Exception:
                pass
            self.layout.note = f"🛑 Sikeresen leállítva: **{info.get('username')}** (`{uid}`)"
        else:
            self.layout.note = "ℹ️ Ez a munkamenet korábban már leállt."
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class StatusLayoutView(TxaView):
    admin_only = True

    def __init__(self, user_id: int, bot, bot_start_time: float, back=None):
        super().__init__(user_id, back)
        self.bot = bot
        self.bot_start_time = bot_start_time
        self.note = None
        self.build()

    def build(self):
        from limits_manager import load_limits, get_pending_transactions, get_price_per_quest
        from completer import get_progress_bar
        from system_info import get_process_memory
        self.clear_items()
        users = config.ACTIVE_USERS
        count = len(users)
        data = load_limits()
        done = data.get("total_completed", config.TOTAL_COMPLETED)
        failed = data.get("total_failed", config.TOTAL_FAILED)
        pending = len(get_pending_transactions())
        on = lambda v: f"{config.EMOJI_SUCCESS} Bekapcsolva" if v else f"{config.EMOJI_FAIL} Kikapcsolva"

        items = [
            f"### 📡 Admin Irányítópult {config.EMOJI_ROCKET}",
            self.note,
            f"{'🟢' if count else '⚪'} **{count:02d}** aktív munkamenet\n"
            f"{config.EMOJI_SUCCESS} **{done:02d}** sikeres  •  ❌ **{failed:02d}** sikertelen  •  🧾 **{pending:02d}** függő számla",
            "---",
        ]
        if not users:
            items.append("**🏃 Aktív munkamenetek**\n💤 *Jelenleg nincs aktív folyamat.*")
        else:
            for uid, info in list(users.items())[:5]:
                completer = info.get("completer")
                curr = getattr(completer, "current_quest_info", None) if completer else None
                if curr:
                    d, n = curr.get("done", 0), curr.get("needed", 1)
                    body = f"🎯 **{curr.get('name', 'Quest')}**\n`{get_progress_bar(d, n)}`  ⏳ `{int(d):02d}s/{int(n):02d}s`"
                else:
                    body = "🔍 *Küldetések vizsgálata / várakozás...*"
                run_s = int(time.time() - info.get("start_timestamp", time.time()))
                g = self.bot.get_guild(info.get("guild_id")) if info.get("guild_id") else None
                where = f"🏠 {g.name}" if g else "🏠 (ismeretlen szerver)"
                items.append(
                    f"**🟢 {info.get('username')}** • <@{uid}> • {where}\n{body}\n"
                    f"🕒 Indult: <t:{info.get('start_timestamp', 0)}:R> • Futásidő: `{run_s // 3600:02d}ó {run_s % 3600 // 60:02d}p`"
                )
            if count > 5:
                items.append(f"-# … és további {count - 5:02d} munkamenet")
            items.append("---")
            row = discord.ui.ActionRow()
            row.add_item(StopSessionSelect(self, self.bot))
            items += ["**🛑 Munkamenet Leállítása**", row]

        uptime = int(time.time() - self.bot_start_time)
        timeout = get_transaction_timeout()
        items += [
            "---",
            f"**💳 Fizetés** • `{get_price_per_quest():,} Ft`/lehetőség • határidő `{timeout // 60:02d}p` • API {'✅' if config.SEPAY_API_KEY else '❌'}\n"
            f"**⚙️ Működés** • lekérdezés `{config.POLL_INTERVAL:02d}s` • heartbeat `{config.HEARTBEAT_INTERVAL:02d}s` • "
            f"auto-elfogadás {on(config.AUTO_ACCEPT)} • Admin bypass {on(getattr(config, 'ADMIN_BYPASS_LIMIT', True))}\n"
            f"**🤖 Bot** • ping `{round(self.bot.latency * 1000)} ms` • uptime `{uptime // 3600:02d}ó {uptime % 3600 // 60:02d}p` • RAM `{get_process_memory()} MB`",
        ]
        self.add_item(box(*items, accent=GREEN if count else ACCENT))

        async def refresh(interaction: discord.Interaction):
            self.note = None
            self.build()
            await interaction.response.edit_message(view=self)

        async def open_config(interaction: discord.Interaction):
            await self.go(interaction, ConfigLayoutView(self.user_id, back=lambda i: self))

        async def open_system(interaction: discord.Interaction):
            view = SystemLayoutView(self.user_id, self.bot, self.bot_start_time, back=lambda i: self)
            await self.go(interaction, view)
            view.start(interaction)

        self.add_item(row_of(
            btn("Frissítés", "🔄", refresh),
            btn("Beállítások", "⚙️", open_config),
            btn("Rendszer", "🖥️", open_system),
        ))


# ───────────────────────── Rendszerfigyelő (Realtime) ─────────────────────────

class SystemLayoutView(TxaView):
    REFRESH_SECONDS = 3
    MAX_LIVE_SECONDS = 14 * 60

    def __init__(self, user_id: int, bot, bot_start_time: float, back=None):
        super().__init__(user_id, back, timeout=None)
        self.bot = bot
        self.bot_start_time = bot_start_time
        self.live = True
        self._interaction = None
        self.build()

    def on_leave(self):
        self.live = False

    def build(self):
        from system_info import (
            get_cpu_info, get_ram_info, get_disk_info, get_process_memory, get_os_info,
            get_active_sessions_count, get_realtime_quest_stats, slider_bar, load_icon,
        )
        import platform
        self.clear_items()
        up = int(time.time() - self.bot_start_time)
        days, rem = divmod(up, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = (f"{days:02d}n " if days else "") + f"{hours:02d}:{mins:02d}:{secs:02d}"

        cpu, ram, disk = get_cpu_info(), get_ram_info(), get_disk_info()
        latency = round(self.bot.latency * 1000) if self.bot.latency is not None else 0
        ok, bad = get_realtime_quest_stats()
        worst = max(cpu["percent"], ram["percent"], disk["percent"])
        accent = GREEN if worst < 60 else YELLOW if worst < 85 else RED
        ping_icon = "🟢" if latency < 150 else "🟡" if latency < 400 else "🔴"

        status = "🔴 **ÉLŐ** • automatikus frissítés 3 másodpercenként" if self.live else "⏸️ Frissítés szüneteltetve"
        self.add_item(box(
            f"### ⚡ Rendszerfigyelő (Telemetria) {config.EMOJI_LOADING}",
            f"{status}\n🕒 Elindult: <t:{int(self.bot_start_time)}:R> • Futásidő: **{uptime_str}**",
            f"```\nCPU   {cpu['percent']:>5.1f}%  {slider_bar(cpu['percent'])}\n"
            f"RAM   {ram['percent']:>5.1f}%  {slider_bar(ram['percent'])}\n"
            f"LEMEZ {disk['percent']:>5.1f}%  {slider_bar(disk['percent'])}\n```",
            "---",
            f"{load_icon(cpu['percent'])} **CPU** • {cpu['cores']:02d} mag • `{str(cpu['model'])[:40]}`\n"
            f"{load_icon(ram['percent'])} **RAM** • {ram['used_gb']}/{ram['total_gb']} GB • Bot memóriahasználat: `{get_process_memory()} MB`\n"
            f"{load_icon(disk['percent'])} **Tárhely** • {disk['used_gb']}/{disk['total_gb']} GB",
            f"{ping_icon} **Késleltetés** `{latency} ms` • 👥 **Munkamenetek** `{get_active_sessions_count():02d}` • 🎯 **Questek** ✅ `{ok:02d}` ❌ `{bad:02d}`",
            f"-# {get_os_info()} • Python {platform.python_version()} • discord.py {discord.__version__} • frissítve {time.strftime('%H:%M:%S')}",
            accent=accent,
        ))

        async def toggle(interaction: discord.Interaction):
            self.live = not self.live
            self.build()
            await interaction.response.edit_message(view=self)
            if self.live:
                self.start(interaction)

        async def refresh(interaction: discord.Interaction):
            self.build()
            await interaction.response.edit_message(view=self)

        self.add_item(self.nav_row(
            refresh=refresh,
            extra=[btn("Szüneteltetés" if self.live else "Élő Folytatás", "⏸️" if self.live else "▶️", toggle,
                       style=discord.ButtonStyle.secondary if self.live else discord.ButtonStyle.success)],
        ))

    def start(self, interaction: discord.Interaction):
        self._interaction = interaction
        task = getattr(self, "_task", None)
        if task and not task.done():
            task.cancel()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        from logger import log
        began = time.time()
        failures = 0
        while self.live and time.time() - began < self.MAX_LIVE_SECONDS:
            await asyncio.sleep(self.REFRESH_SECONDS)
            if not self.live:
                return
            try:
                self.build()
                await self._interaction.edit_original_response(view=self)
                failures = 0
            except asyncio.CancelledError:
                raise
            except (discord.NotFound, discord.Forbidden):
                self.live = False
                return
            except Exception as e:
                failures += 1
                log(f"Rendszerfigyelő hiba ({failures}): {e}", "warn")
                if failures >= 5:
                    break
        if self.live:
            self.live = False
            try:
                self.build()
                await self._interaction.edit_original_response(view=self)
            except Exception:
                pass


# ───────────────────────── Statisztikák ─────────────────────────

def _history_content(user_id: int, limit: int = 10):
    from limits_manager import get_payment_history
    rows = get_payment_history(user_id)
    if not rows:
        return "🧾 Befizetési Előzmények", "Még nincsenek tranzakcióid. Használd a `/buy` parancsot az első vásárláshoz!"
    lines = [f"`{r['order_id']}` • **{r['amount']:,} Ft** • +{r['quests']:02d} lehetőség • <t:{r['paid_at']}:R>" for r in reversed(rows[-limit:])]
    return "🧾 Befizetési Előzmények", "\n".join(lines) + f"\n\n💰 Összesen befizetve: **{sum(r['amount'] for r in rows):,} Ft**"


def _leaderboard_content():
    from limits_manager import get_leaderboard
    rows = get_leaderboard(10)
    medals = ["🥇", "🥈", "🥉"]
    if not rows:
        return "🏆 Quest Ranglista", "Még senki nem fejezett be küldetést a rendszeren keresztül."
    body = "\n".join(f"{medals[i] if i < 3 else f'`#{i + 1:02d}`'} <@{u}> — **{t:02d}** quest" for i, (u, t) in enumerate(rows))
    return "🏆 Quest Ranglista", body + f"\n\n🎯 Rendszerszinten teljesítve: **{config.TOTAL_COMPLETED:02d}** küldetés"


def _quota_content(user_id: int):
    from limits_manager import get_user_quota_info
    info = get_user_quota_info(user_id)
    return "💰 Egyenleged", f"💎 Elérhető lehetőségek: **{info['quota_str']}**\n🏆 Befejezett küldetések: `{info['total_completed']:02d} db`"


def history_view(user_id, back=None, limit: int = 10):
    return SimpleLayoutView(user_id, *_history_content(user_id, limit), accent=YELLOW, back=back,
                            refresh=lambda: _history_content(user_id, limit))


def leaderboard_view(user_id, back=None):
    return SimpleLayoutView(user_id, *_leaderboard_content(), accent=YELLOW, back=back, refresh=_leaderboard_content)


def quota_view(user_id, back=None):
    return SimpleLayoutView(user_id, *_quota_content(user_id), accent=GREEN, back=back,
                            refresh=lambda: _quota_content(user_id))


def ping_view(user_id, bot, back=None):
    def content():
        ms = round(bot.latency * 1000)
        return "🏓 Pong!", f"Gateway késleltetés: **{ms} ms**\nAktív munkamenetek: **{len(config.ACTIVE_USERS):02d}**"
    ms = round(bot.latency * 1000)
    accent = GREEN if ms < 150 else ORANGE if ms < 400 else RED
    return SimpleLayoutView(user_id, *content(), accent=accent, back=back, refresh=content)


# ───────────────────────── Súgó (Help) ─────────────────────────

HELP_GROUPS = {
    "quest": ("Questek", "🚀", "Küldetések indítása, követése és leállítása.",
              ["start", "details", "my_quests", "user_info", "stop_my_session"]),
    "pay": ("Egyenleg & Bolt", "💎", "Lehetőségek vásárlása, egyenleg és előzmények megtekintése.",
            ["menu", "buy", "history"]),
    "info": ("Információ", "📊", "Ranglista, bot késleltetés, telemetria és meghívó.",
             ["leaderboard", "ping", "system_stats", "invite", "help"]),
    "admin": ("Adminisztráció", "🛠️", "Irányítás: státusz, beállítások, kvóták és csatorna konfiguráció.",
              ["status", "config", "set_limit", "add_balance", "set_price", "set_timeout",
               "stop_user", "channel", "setup_here", "clear_cache", "qr_test"]),
}


def _command_info(name: str):
    try:
        import bot as botmod
        cmd = botmod.bot.tree.get_command(name)
    except Exception:
        cmd = None
    if not cmd:
        return "", []
    params = [(p.name, p.description or "", p.required) for p in getattr(cmd, "parameters", [])]
    return cmd.description or "", params


class HelpGroupSelect(discord.ui.Select):
    def __init__(self, layout, groups):
        options = [
            discord.SelectOption(label=g[0], value=k, emoji=g[1], description=g[2][:100])
            for k, g in groups.items()
        ]
        super().__init__(placeholder="📂 Válassz egy parancscsoportot", options=options)
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        self.layout.group, self.layout.command = self.values[0], None
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class HelpCommandSelect(discord.ui.Select):
    def __init__(self, layout, names, selected):
        options = []
        for n in names:
            desc, _ = _command_info(n)
            options.append(discord.SelectOption(label=f"/{n}", value=n, description=(desc or "—")[:100], default=(n == selected)))
        super().__init__(placeholder="🔎 Válassz egy parancsot a részletekért", options=options[:25])
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        self.layout.command = self.values[0]
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class HelpLayoutView(TxaView):
    def __init__(self, user_id: int, back=None):
        super().__init__(user_id, back)
        self.group = None
        self.command = None
        self.build()

    def _groups(self):
        is_admin = self.user_id in config.ADMIN_IDS
        return {k: g for k, g in HELP_GROUPS.items() if k != "admin" or is_admin}

    def build(self):
        self.clear_items()
        groups = self._groups()
        if self.group not in groups:
            self.group, self.command = None, None

        if self.group is None:
            total = sum(len(g[3]) for g in groups.values())
            row = discord.ui.ActionRow()
            row.add_item(HelpGroupSelect(self, groups))
            items = [
                f"### 📖 Súgóközpont • Quest Bot {config.EMOJI_ROCKET}",
                "Automatikus felmérés • elfogadás • teljesítés a Discord Questekhez.",
                "\n".join(f"{g[1]} **{g[0]}** • {len(g[3]):02d} parancs\n-# {g[2]}" for g in groups.values()),
                "---",
                "**Válassz parancscsoportot**",
                row,
                f"-# Összesen {total:02d} parancs • Quest Bot v4.0",
            ]
        else:
            label, emoji, desc, names = groups[self.group]
            lines = []
            for n in names:
                d, _ = _command_info(n)
                lines.append(f"`/{n}` — {d or '—'}")
            row = discord.ui.ActionRow()
            row.add_item(HelpCommandSelect(self, names, self.command))
            items = [f"### {emoji} {label}", desc, "\n".join(lines), "---"]
            if self.command:
                d, params = _command_info(self.command)
                admin_cmd = self.command in HELP_GROUPS["admin"][3]
                detail = f"**/{self.command}**\n{d or '—'}\n👤 Hozzáférés: {'🛠️ Csak Admin' if admin_cmd else '🌐 Mindenki'}"
                if params:
                    detail += "\n**Paraméterek:**\n" + "\n".join(
                        f"• `{n}`{'' if req else ' (opcionális)'} — {pd or '—'}" for n, pd, req in params)
                else:
                    detail += "\n**Paraméterek:** nincsenek"
                items += [detail, "---"]
            items += ["**Részletes parancsinformáció**", row]
        self.add_item(box(*items))

        async def go_home(interaction: discord.Interaction):
            self.group, self.command = None, None
            self.build()
            await interaction.response.edit_message(view=self)

        async def open_ext(interaction: discord.Interaction):
            from bot import send_extension_guide
            await send_extension_guide(interaction)

        async def open_buy(interaction: discord.Interaction):
            from payment_views import get_limit_embed_and_view, register_invoice
            _, view = get_limit_embed_and_view(self.user_id, back=lambda i: self)
            await self.go(interaction, view)
            register_invoice(interaction, view)

        buttons = []
        if self.group is not None:
            buttons.append(btn("Vissza", "◀️", go_home))
        elif self.back:
            buttons.append(btn("Vissza", "◀️", self._on_back_click))
        buttons += [
            btn("QR Útmutató", "📱", open_ext, style=discord.ButtonStyle.primary),
            btn("Vásárlás", "💳", open_buy, style=discord.ButtonStyle.success),
        ]
        buttons += link_buttons("invite", "quest")
        self.add_item(row_of(*buttons))


# ───────────────────────── QR-Kódos Használati Útmutató ─────────────────────────


class ExtensionGuideView(TxaView):
    def __init__(self, user_id: int, has_file: bool = False):
        super().__init__(user_id, timeout=900)
        steps = (
            "### 📱 Mobil QR-Kódos Bejelentkezés (Zéró Manuális Token!)\n"
            "**1️⃣ Indítás a csatornában**\n"
            "Kattints a zöld 🚀 **Indítás** gombra a bot kijelölt működési csatornájában.\n\n"
            "**2️⃣ Nyisd meg a mobil Discord appot**\n"
            "Nyisd meg a hivatalos Discord alkalmazást az okostelefonodon.\n\n"
            "**3️⃣ Nyisd meg a beolvasót**\n"
            "Koppints a jobb alsó sarokban a profilképedre → **Beállítások ➔ QR-kód beolvasása** menüpontra.\n\n"
            "**4️⃣ Beolvasás & Megerősítés**\n"
            "Irányítsd a kamerát a bot által generált QR-kódra, majd a telefonodon koppints az **'Igen, bejelentkezem'** gombra.\n\n"
            "**5️⃣ Automata Teljesítés**\n"
            "A rendszer azonnal elindítja a küldetések szimulációját a háttérben, és privát üzenetben (DM) élőben közvetíti a haladást!\n"
        )
        items = [
            f"### 🚀 Használati Útmutató — Quest Teljesítés {config.EMOJI_ROCKET}",
            "A leggyorsabb és legbiztonságosabb módja a Discord küldetések automatikus teljesítésének:",
            "---",
            steps,
            "---",
            "🔒 **Maximális Biztonság:** A bejelentkezés a Discord hivatalos Remote Auth technológiáját használja végpontok közötti RSA-2048 titkosítással. Nem szükséges manuálisan megadnod semmilyen tokent!",
        ]
        self.add_item(box(*items))

        self.add_item(row_of(
            discord.ui.Button(label="Discord Web Megnyitása", emoji="💬", style=discord.ButtonStyle.link, url="https://discord.com/app"),
            discord.ui.Button(label="Quest Oldal Megnyitása", emoji="🎁", style=discord.ButtonStyle.link, url=QUEST_HOME_URL),
        ))


# ───────────────────────── Jutalom Átvételi Eredmény ─────────────────────────

def claim_result_view(user_id: int, kind: str, quest_name: str, code: str = None, detail: str = None) -> TxaView:
    view = TxaView(user_id, timeout=600)
    link_row = row_of(
        discord.ui.Button(label="Quest Oldal Megnyitása", emoji="🎁", style=discord.ButtonStyle.link, url=QUEST_HOME_URL),
        discord.ui.Button(label="Discord Web Megnyitása", emoji="💬", style=discord.ButtonStyle.link, url="https://discord.com/app"),
    )
    if kind == "success":
        body = (f"🔑 **Promóciós Ajándékkód:**\n```\n{code}\n```\n💡 Másold ki a kódot, és váltsd be a **Beállítások ➔ Gift Inventory** menüben vagy a játékban."
                if code else "✨ A jutalom közvetlenül jóváíródott a Discord fiókodban!")
        view.add_item(box(f"### 🎉 Sikeres Jutalom Beváltás! {config.EMOJI_SUCCESS}", f"🎯 **Küldetés:** {quest_name}", "---", body, accent=GREEN))
    elif kind == "captcha":
        view.add_item(box(
            f"### 🛡️ Kézi Beváltás Szükséges a Discordon {config.EMOJI_LOADING}",
            f"🎯 **Küldetés:** {quest_name}\n🎉 A küldetés **100%**-ban elkészült — a jutalom biztonságban elérhető a fiókodban.",
            "---",
            "**❓ Miért nem vette át a bot automatikusan?**\n"
            "A Discord ennél a küldetésnél **hCaptcha ellenőrzést** kér az ajándék átvételekor. Ezt kizárólag a böngésződben tudod megcsinálni egyetlen kattintással.",
            "---",
            "**⚡ Beváltás 10 másodperc alatt:**\n"
            "1️⃣ Kattints az alábbi **Quest Oldal Megnyitása** gombra.\n"
            "2️⃣ Keresd meg a befejezett küldetést és kattints a **Claim Reward (Jutalom Átvétele)** gombra.\n"
            "3️⃣ Ha képrejtvény jelenik meg, oldd meg, és a kód azonnal megjelenik.",
            "-# 💡 Ha nem találod: Discord Beállítások ➔ Gift Inventory (Ajándékok).",
            accent=YELLOW,
        ))
        view.add_item(link_row)
        return view
    else:
        view.add_item(box(
            f"### Nem Sikerült Átvenni a Jutalmat {config.EMOJI_FAIL}",
            f"🎯 **Küldetés:** {quest_name}",
            f"```\n{(detail or 'Ismeretlen hiba')[:500]}\n```",
            "Kérjük, próbáld újra pár perc múlva, vagy vedd át közvetlenül a Discord Quest felületén.",
            accent=RED,
        ))
    view.add_item(link_row)
    return view


def text_card(user_id: int, title: str, body: str, accent=GREEN, footer: str = None, links=()) -> TxaView:
    view = TxaView(user_id, timeout=300)
    view.add_item(box(f"### {title}", body, f"-# {footer}" if footer else None, accent=accent))
    btns = link_buttons(*links)
    if btns:
        view.add_item(row_of(*btns))
    return view


def embed_to_view(embed: discord.Embed, user_id: int, links=()) -> TxaView:
    lines = []
    for f in embed.fields:
        lines.append(f"**{f.name}**\n{f.value}")
    accent = embed.color if embed.color else ACCENT
    footer = embed.footer.text if embed.footer and embed.footer.text else None
    view = TxaView(user_id, timeout=300)
    view.add_item(box(
        f"### {embed.title or 'Értesítés'}",
        embed.description or None,
        "---" if lines and embed.description else None,
        "\n\n".join(lines) if lines else None,
        f"-# {footer}" if footer else None,
        accent=accent,
    ))
    btns = link_buttons(*links)
    if btns:
        view.add_item(row_of(*btns))
    return view


def invite_view(user_id: int) -> TxaView:
    view = TxaView(user_id, timeout=300)
    view.add_item(box(
        f"### ➕ Quest Bot Hozzáadása a Szerveredhez {config.EMOJI_ROCKET}",
        "Kattints a **Bot Meghívása** gombra a bot hozzáadásához a Discord szerveredre.",
        "---",
        "**✨ Nincs szükség bonyolult beállításra:**\n"
        "• A bot automatikusan megkeresi a legmegfelelőbb csatornát és kiteszi az interaktív **Vezérlőpanelt**.\n"
        "• A tagok egyszerűen rákattintanak az 🚀 **Indítás** gombra, vagy használhatják a `/help` parancsot.\n"
        "• Ha a panel nem jelenik meg, ellenőrizd, hogy a botnak megvannak-e a szükséges jogosultságai (Üzenetküldés, Beágyazott hivatkozások, Előzmények olvasása).",
        accent=ACCENT,
    ))
    btns = link_buttons("invite", "quest", "discord")
    if btns:
        view.add_item(row_of(*btns))
    return view


# ───────────────────────── Központi Menü (/menu) ─────────────────────────

def build_tree():
    from limits_manager import PACKAGES, get_package_info
    return {
        "buy": {
            "label": "Quest Lehetőségek Vásárlása", "emoji": "💎", "desc": "További lehetőségek vásárlása",
            "children": {
                str(n): {
                    "label": f"{n:02d} lehetőség - {info['final']:,} Ft",
                    "emoji": PACKAGE_EMOJI.get(n, "💎"),
                    "desc": (f"{info['pct']}% kedvezmény • megtakarítás: {info['saved']:,} Ft" if info["pct"] else "Alapcsomag, kedvezmény nélkül"),
                    "children": {"qr": {"label": "Fizetés indítása", "emoji": "🏦", "desc": "Fizetési adatok és QR-kód megjelenítése"}},
                }
                for n, info in ((n, get_package_info(n)) for n in PACKAGES)
            },
        },
        "stats": {
            "label": "Statisztika & Egyenleg", "emoji": "📊", "desc": "Egyenleg, előzmények, ranglista",
            "children": {
                "quota": {"label": "Saját egyenlegem", "emoji": "💰", "desc": "Elérhető lehetőségek megtekintése"},
                "history": {
                    "label": "Befizetési előzmények", "emoji": "🧾", "desc": "Korábbi tranzakciók listája",
                    "children": {
                        "5": {"label": "Utolsó 05 tranzakció", "emoji": "5️⃣", "desc": "Gyors áttekintés"},
                        "10": {"label": "Utolsó 10 tranzakció", "emoji": "🔟", "desc": "Részletesebb lista"},
                    },
                },
                "top": {"label": "Ranglista", "emoji": "🏆", "desc": "A legtöbb küldetést teljesítő felhasználók"},
            },
        },
        "help": {
            "label": "Segítség & Útmutató", "emoji": "🛠️", "desc": "Leírás és működés",
            "children": {
                "guide": {"label": "Használati útmutató", "emoji": "📱", "desc": "QR-kódos mobil bejelentkezés útmutatója"},
                "how": {"label": "Hogyan működik a bot?", "emoji": "❓", "desc": "A küldetés-automatizálás működése"},
            },
        },
    }


class MenuLevelSelect(discord.ui.Select):
    def __init__(self, layout, level: int, placeholder: str, nodes: dict, selected):
        options = [
            discord.SelectOption(label=n["label"], value=k, emoji=n.get("emoji"),
                                 description=(n.get("desc") or None), default=(k == selected))
            for k, n in nodes.items()
        ]
        super().__init__(placeholder=placeholder, options=options)
        self.layout = layout
        self.level = level

    async def callback(self, interaction: discord.Interaction):
        self.layout.picks = self.layout.picks[: self.level] + [self.values[0]]
        self.layout.build()
        await interaction.response.edit_message(view=self.layout)


class MenuLayoutView(TxaView):
    HEADINGS = ["**① Főkategória**", "**② Alkategória**", "**③ Opciók**"]
    PLACEHOLDERS = ["Válassz főkategóriát...", "Válassz alkategóriát...", "Válassz opciót..."]

    def __init__(self, user_id: int, back=None):
        super().__init__(user_id, back)
        self.tree = build_tree()
        self.picks = []
        self.build()

    def _path(self):
        nodes, level = [], self.tree
        for key in self.picks:
            node = level.get(key)
            if not node:
                break
            nodes.append(node)
            level = node.get("children") or {}
        return nodes

    def _ready(self) -> bool:
        nodes = self._path()
        return bool(nodes) and (len(self.picks) == 3 or not nodes[-1].get("children"))

    def build(self):
        self.clear_items()
        nodes = self._path()
        crumbs = " ➜ ".join(f"{n.get('emoji', '')} {n['label']}" for n in nodes) or "*Nincs kiválasztva*"
        items = [f"### 🧭 Quest Bot Vezérlőközpont {config.EMOJI_ROCKET}",
                 "Válassz az opciók közül a navigációhoz.",
                 f"**📍 Útvonal:** {crumbs}", "---"]
        level_nodes = self.tree
        for level in range(3):
            if not level_nodes:
                break
            selected = self.picks[level] if level < len(self.picks) else None
            row = discord.ui.ActionRow()
            row.add_item(MenuLevelSelect(self, level, self.PLACEHOLDERS[level], level_nodes, selected))
            items += [self.HEADINGS[level], row]
            if selected is None:
                break
            level_nodes = (level_nodes.get(selected) or {}).get("children")
        items.append("-# ✅ Készen áll — kattints a Jóváhagyás gombra" if self._ready() else "-# Válassz a megjelenő menüből")
        self.add_item(box(*items))

        async def reset(interaction: discord.Interaction):
            self.picks = []
            self.build()
            await interaction.response.edit_message(view=self)

        self.add_item(self.nav_row(extra=[
            btn("Jóváhagyás", config.EMOJI_SUCCESS, self._confirm, style=discord.ButtonStyle.success, disabled=not self._ready()),
            btn("Alaphelyzet", "🔄", reset, disabled=not self.picks),
        ]))

    async def _confirm(self, interaction: discord.Interaction):
        from limits_manager import create_transaction
        uid, top = self.user_id, self.picks[0]
        me = lambda i: self
        if top == "buy":
            from payment_views import get_limit_embed_and_view, register_invoice
            if not create_transaction(uid, int(self.picks[1])):
                await interaction.response.send_message(
                    f"{config.EMOJI_FAIL} Már van egy folyamatban lévő tranzakciód. Használd a `/buy` parancsot a megtekintéséhez vagy lemondásához.", ephemeral=True)
                return
            _, view = get_limit_embed_and_view(uid, back=me)
            await self.go(interaction, view)
            register_invoice(interaction, view)
            return
        if top == "stats":
            sub = self.picks[1]
            view = (quota_view(uid, back=me) if sub == "quota"
                    else history_view(uid, back=me, limit=int(self.picks[2])) if sub == "history"
                    else leaderboard_view(uid, back=me))
            await self.go(interaction, view)
            return
        if self.picks[1] == "guide":
            from bot import send_extension_guide
            await send_extension_guide(interaction)
            return
        view = SimpleLayoutView(
            uid, "❓ Hogyan Működik?",
            "1️⃣ Kattints az 🚀 **Indítás** gombra a bot kijelölt csatornájában.\n"
            "2️⃣ Olvasd be a megjelenő QR-kódot a telefonos Discord appoddal (**Beállítások ➔ QR-kód beolvasása**).\n"
            "3️⃣ Erősítsd meg a bejelentkezést a telefonodon (**'Igen, bejelentkezem'**).\n"
            "4️⃣ A bot automatikusan **beolvassa • elfogadja • teljesíti** a küldetéseket és privát üzenetben tájékoztat a haladásról.\n"
            "5️⃣ Minden nap jár 1 ingyenes lehetőség, további lehetőségek a `/menu` vagy `/buy` parancsnál érhetők el.",
            back=me,
        )
        await self.go(interaction, view)
