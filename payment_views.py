import asyncio
import time

import aiohttp
import discord

import config
from txa_layout import (
    TxaView, box, row_of, btn, _resolve, MISSING, PACKAGE_EMOJI,
    GREEN, YELLOW, ORANGE, RED,
)
from limits_manager import (
    get_active_transaction,
    create_transaction,
    cancel_transaction,
    fetch_sepay_transactions,
    match_payment,
    get_pending_transactions,
    complete_transaction,
    get_price_per_quest,
    get_package_info,
    PACKAGES,
    get_transaction_timeout,
    get_user_quota_info,
)

LIVE_INVOICES = {}
AUTO_CHECK_INTERVAL = 6


def qr_url(order_id: str, amount: int) -> str:
    holder = config.ACCOUNT_NAME.replace(' ', '%20')
    return (
        f"https://qr.sepay.vn/img?bank={config.BANK_ID}&acc={config.ACCOUNT_NO}"
        f"&template=compact&amount={amount}&des={order_id}&holder={holder}"
    )


def register_invoice(interaction: discord.Interaction, view):
    if isinstance(view, InvoiceLayoutView):
        LIVE_INVOICES[view.order_id] = interaction
        view.start(interaction)


def build_success_view(tx: dict, user_id: int, auto: bool) -> TxaView:
    info = get_user_quota_info(user_id)
    view = TxaView(user_id, timeout=60)
    view.add_item(box(
        f"### 🎉 SIKERES FIZETÉS! {config.EMOJI_SUCCESS}",
        f"{config.EMOJI_SUCCESS} **A rendszer jóváírta a befizetést!**\n"
        f"{'⚡ A rendszer **automatikusan észlelte** a befizetést.' if auto else 'A tranzakció sikeresen ellenőrizve és jóváhagyva.'}",
        "---",
        f"📝 **Rendelési azonosító:** `{tx['order_id']}`\n"
        f"💰 **Befizetett összeg:** `{tx['amount']:,} Ft`\n"
        f"📦 **Jóváírt lehetőségek:** `+{tx['quests_to_buy']:02d} db`\n"
        f"💎 **Jelenlegi elérhető egyenleg:** **{info['quota_str']}**",
        "🚀 Használd a `/details` parancsot az állapot követéséhez vagy a `/history` parancsot az előzményekhez.",
        "-# ⏱️ Ez az üzenet 10 másodperc múlva automatikusan bezárul • Köszönjük!",
        accent=GREEN,
    ))
    return view


def _schedule_delete(interaction: discord.Interaction, delay: int = 10):
    async def task():
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
    asyncio.create_task(task())


async def payment_watcher(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            pending = get_pending_transactions()
            if pending and config.SEPAY_API_KEY:
                tx_list = await fetch_sepay_transactions()
                for user_id, tx in pending:
                    if not match_payment(tx_list, tx["order_id"], tx["amount"]):
                        continue
                    done = complete_transaction(user_id, tx["order_id"])
                    if not done:
                        continue
                    success = build_success_view(done, user_id, auto=True)
                    inter = LIVE_INVOICES.pop(done["order_id"], None)
                    edited = False
                    if inter:
                        try:
                            await inter.edit_original_response(view=success)
                            _schedule_delete(inter)
                            edited = True
                        except Exception:
                            pass
                    if not edited:
                        try:
                            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                            await user.send(view=success)
                        except Exception:
                            pass
        except Exception as e:
            from logger import log
            log(f"Fizetésfigyelő hiba: {e}", "warn")
        await asyncio.sleep(AUTO_CHECK_INTERVAL)


# ───────────────────────── Csomagválasztó / Bolt ─────────────────────────

class PackageSelect(discord.ui.Select):
    def __init__(self, layout):
        options = []
        for qty in PACKAGES:
            info = get_package_info(qty)
            desc = f"{info['pct']:02d}% kedvezmény • megtakarítás: {info['saved']:,} Ft" if info["pct"] else "Alapcsomag, kedvezmény nélkül"
            options.append(discord.SelectOption(
                label=f"{qty:02d} Quest lehetőség - {info['final']:,} Ft",
                value=str(qty), emoji=PACKAGE_EMOJI.get(qty, "💎"), description=desc
            ))
        super().__init__(placeholder="🛒 Kattints ide a csomag kiválasztásához", options=options)
        self.layout = layout

    async def callback(self, interaction: discord.Interaction):
        user_id = self.layout.user_id
        quantity = int(self.values[0])

        loading = TxaView(user_id, timeout=60)
        loading.add_item(box(
            f"### {config.EMOJI_LOADING} Tranzakció Előkészítése",
            f"{config.EMOJI_LOADING} Rendelés és fizetési adatok generálása, kérjük várj...",
        ))
        await interaction.response.edit_message(view=loading)

        tx = create_transaction(user_id, quantity)
        if not tx:
            await interaction.followup.send(
                "❌ **Már van egy befejezetlen tranzakciód!** Kérjük fizesd be vagy kattints a **Tranzakció Lemondása** gombra az új rendeléshez.",
                ephemeral=True
            )
            _, view = get_limit_embed_and_view(user_id, back=self.layout.back)
            await interaction.edit_original_response(view=view)
            register_invoice(interaction, view)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(qr_url(tx["order_id"], tx["amount"]), timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        await r.read()
        except Exception:
            pass

        _, view = get_limit_embed_and_view(user_id, back=self.layout.back)
        await interaction.edit_original_response(view=view)
        register_invoice(interaction, view)


class ShopLayoutView(TxaView):
    def __init__(self, user_id: int, back=None):
        super().__init__(user_id, back, timeout=300)
        self.build()

    def build(self):
        self.clear_items()
        info = get_user_quota_info(self.user_id)
        lines = []
        for qty in PACKAGES:
            p = get_package_info(qty)
            emoji = PACKAGE_EMOJI.get(qty, "💎")
            if p["pct"]:
                lines.append(f"{emoji} **{qty:02d} lehetőség** • ~~{p['original']:,} Ft~~ ➜ **{p['final']:,} Ft** `-{p['pct']:02d}%`")
            else:
                lines.append(f"{emoji} **{qty:02d} lehetőség** • **{p['final']:,} Ft**")
        row = discord.ui.ActionRow()
        row.add_item(PackageSelect(self))
        self.add_item(box(
            f"### 💎 Quest Bolt & Egyenleg {config.EMOJI_ROCKET}",
            f"{config.EMOJI_ROCKET} Vásárolj további lehetőségeket az automatikus Quest teljesítéshez.",
            f"👤 <@{self.user_id}>\n💎 Elérhető lehetőségek: **{info['quota_str']}**\n🏆 Befejezett küldetések: `{info['total_completed']:02d} db`",
            "---",
            f"**🏷️ Árlista • {get_price_per_quest():,} Ft / lehetőség**\n" + "\n".join(lines),
            "🎁 Minél nagyobb csomagot választasz, annál nagyobb a kedvezmény!",
            "---",
            "**🛒 Csomag kiválasztása**",
            row,
            "-# ✨ A megvásárolt lehetőségek sosem járnak le és korlátlan ideig felhasználhatók.",
        ))

        async def refresh(interaction: discord.Interaction):
            self.build()
            await interaction.response.edit_message(view=self)

        self.add_item(self.nav_row(
            refresh=refresh,
            help_text="ℹ️ Válassz egy csomagot a menüből a fizetési adatok létrehozásához. A lehetőségek a fizetés beérkezése után automatikusan jóváíródnak.",
        ))


# ───────────────────────── Számla / Tranzakció ─────────────────────────

class ConfirmLeaveView(TxaView):
    def __init__(self, user_id: int, tx: dict, back):
        super().__init__(user_id, back, timeout=120)
        expire_ts = tx["created_at"] + get_transaction_timeout()
        self.add_item(box(
            "### ⚠️ Befejezetlen Fizetés",
            f"Folyamatban van a(z) `{tx['order_id']}` (**{tx['amount']:,} Ft**) azonosítójú tranzakciód.",
            f"• Ha most visszalépsz, a fizetési kérelem **továbbra is érvényes** marad eddig: <t:{expire_ts}:T> (<t:{expire_ts}:R>).\n"
            f"• Ha ezen idő alatt befizeted, a rendszer **automatikusan jóváírja** az egyenleget.\n"
            f"• Ha törölni szeretnéd a rendelést, válaszd a **Tranzakció Lemondása** lehetőséget.",
            "**Biztosan vissza szeretnél lépni?**",
            accent=ORANGE,
        ))

        async def leave(interaction: discord.Interaction):
            view = await _resolve(self.back, interaction)
            await interaction.response.edit_message(view=view)

        async def stay(interaction: discord.Interaction):
            _, view = get_limit_embed_and_view(self.user_id, back=self.back)
            await interaction.response.edit_message(view=view)
            register_invoice(interaction, view)

        self.add_item(row_of(
            btn("Visszalépés", "◀️", leave, style=discord.ButtonStyle.primary),
            btn("Maradás a Számlánál", "💳", stay, style=discord.ButtonStyle.success),
        ))


class InvoiceLayoutView(TxaView):
    REFRESH_SECONDS = 3

    def __init__(self, user_id: int, tx: dict, back=None):
        super().__init__(user_id, back, timeout=get_transaction_timeout() + 60)
        self.tx = tx
        self.order_id = tx["order_id"]
        self.amount = tx["amount"]
        self.quantity = tx["quests_to_buy"]
        self.live = True
        self._interaction = None
        self._task = None
        self.build()

    def on_leave(self):
        self.live = False
        if self._task and not self._task.done():
            self._task.cancel()

    def start(self, interaction: discord.Interaction):
        self._interaction = interaction
        if self._task and not self._task.done():
            self._task.cancel()
        if self.live:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            while self.live:
                await asyncio.sleep(self.REFRESH_SECONDS)
                if not self.live:
                    return
                expire_ts = self.tx.get("created_at", 0) + get_transaction_timeout()
                tx = get_active_transaction(self.user_id)
                if not tx or tx.get("order_id") != self.order_id:
                    self.live = False
                    if time.time() >= expire_ts:
                        LIVE_INVOICES.pop(self.order_id, None)
                        self._build_expired()
                        if self._interaction:
                            await self._interaction.edit_original_response(view=self)
                    return
                rem = expire_ts - int(time.time())
                if rem <= 0:
                    self.live = False
                    cancel_transaction(self.user_id)
                    LIVE_INVOICES.pop(self.order_id, None)
                    self._build_expired()
                    if self._interaction:
                        await self._interaction.edit_original_response(view=self)
                    return
                self.build()
                if self._interaction:
                    await self._interaction.edit_original_response(view=self)
        except (discord.NotFound, discord.HTTPException):
            self.live = False
        except Exception:
            pass

    def _build_expired(self):
        self.clear_items()
        self.add_item(box(
            f"### ⌛ A Számla Lejárt {config.EMOJI_FAIL}",
            f"A(z) `{self.order_id}` rendelési azonosító túllépte a fizetési határidőt ({get_transaction_timeout() // 60:02d} perc).",
            "Ez a rendelés érvényét vesztette. Kérjük, térj vissza a boltba egy új csomag kiválasztásához.",
            accent=RED,
        ))
        async def go_shop(interaction: discord.Interaction):
            _, view = get_limit_embed_and_view(self.user_id, back=self.back)
            await interaction.response.edit_message(view=view)
        self.add_item(row_of(btn("Új Rendelés", "🔄", go_shop, style=discord.ButtonStyle.primary)))

    def build(self):
        self.clear_items()
        info = get_user_quota_info(self.user_id)
        pkg = get_package_info(self.quantity)
        expire_ts = self.tx["created_at"] + get_transaction_timeout()
        rem = max(0, expire_ts - int(time.time()))
        rem_m, rem_s = divmod(rem, 60)
        total = max(1, get_transaction_timeout())
        filled = round(10 * rem / total)
        bar = "🟩" * filled + "⬛" * (10 - filled) if rem > total * 0.3 else "🟥" * filled + "⬛" * (10 - filled)
        deadline = f"`{rem_m:02d}:{rem_s:02d}` (lejár ekkor: <t:{expire_ts}:T>)\n{bar}"
        discount = f" • kedvezmény: {pkg['pct']:02d}%" if pkg["pct"] else ""
        url = qr_url(self.order_id, self.amount)
        self.add_item(box(
            f"### 💳 Fizetési Tájékoztató {config.EMOJI_LOADING}",
            "Olvasd be a QR-kódot vagy utalj közvetlenül az alábbi adatokkal.\n"
            f"🔗 *[Kattints ide a QR-kód nagy méretben való megnyitásához]({url})*",
            f"{config.EMOJI_LOADING} **Fizetésre vár • hátralévő idő:** {deadline}",
            "---",
            f"📝 **Rendelési azonosító / Megjegyzés:** `{self.order_id}`\n"
            f"💰 **Fizetendő összeg:** `{self.amount:,} Ft`\n"
            f"📦 **Vásárolt csomag:** +{self.quantity:02d} quest lehetőség{discount}",
            discord.ui.MediaGallery(discord.MediaGalleryItem(url, description=f"QR {self.order_id}")),
            "---",
            f"💎 Jelenlegi elérhető egyenleg: **{info['quota_str']}** • Teljesítve: `{info['total_completed']:02d}`",
            f"-# 🔄 A rendszer {AUTO_CHECK_INTERVAL:02d} másodpercenként ellenőrzi a fizetést. "
            f"Kérjük, pontosan add meg az azonosítót: `{self.order_id}`.",
            accent=YELLOW,
        ))
        items = [
            btn("Ellenőrzés Most", config.EMOJI_SUCCESS, self._check, style=discord.ButtonStyle.success),
            btn("Tranzakció Lemondása", config.EMOJI_FAIL, self._cancel, style=discord.ButtonStyle.danger),
            btn("Másolás", "📋", self._copy),
            discord.ui.Button(label="QR Megnyitása", emoji="💳", style=discord.ButtonStyle.link, url=url),
        ]
        if self.back:
            items.append(btn("Vissza", "◀️", self._leave))
        self.add_item(row_of(*items))

    async def _check(self, interaction: discord.Interaction):
        self._interaction = interaction
        tx = get_active_transaction(self.user_id)
        if not tx or tx.get("order_id") != self.order_id:
            self.live = False
            _, view = get_limit_embed_and_view(self.user_id, back=self.back)
            await interaction.response.edit_message(view=view)
            await interaction.followup.send(
                "❌ **A tranzakció lejárt vagy már fel lett dolgozva!** Kérjük, válassz egy csomagot az új rendeléshez.", ephemeral=True)
            return

        await interaction.response.defer()
        LIVE_INVOICES[self.order_id] = interaction
        tx_list = await fetch_sepay_transactions()
        if match_payment(tx_list, self.order_id, self.amount):
            done = complete_transaction(self.user_id, self.order_id)
            if done:
                self.live = False
                LIVE_INVOICES.pop(self.order_id, None)
                await interaction.edit_original_response(view=build_success_view(done, self.user_id, auto=False))
                _schedule_delete(interaction)
            return

        await interaction.followup.send(
            f"⏳ **Befizetés még nem érkezett be.**\n"
            f"Nem található beérkező utalás a(z) `{self.order_id}` azonosítóval (**{self.amount:,} Ft**).\n"
            f"💡 Nem szükséges újra kattintani — a rendszer **automatikusan ellenőrzi {AUTO_CHECK_INTERVAL:02d} másodpercenként**, és amint megérkezik, jóváírja.",
            ephemeral=True
        )

    async def _copy(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"📋 **Fizetési adatok** (hosszan nyomva másolható)\n"
            f"Összeg:\n```{self.amount} Ft```"
            f"Közlemény / Azonosító (pontosan megadandó):\n```{self.order_id}```",
            ephemeral=True
        )

    async def _cancel(self, interaction: discord.Interaction):
        self.live = False
        cancel_transaction(self.user_id)
        LIVE_INVOICES.pop(self.order_id, None)
        _, view = get_limit_embed_and_view(self.user_id, back=self.back)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send("🛑 Az aktuális tranzakció lemondva.", ephemeral=True)

    async def _leave(self, interaction: discord.Interaction):
        self.live = False
        tx = get_active_transaction(self.user_id)
        if not tx or tx.get("order_id") != self.order_id:
            view = await _resolve(self.back, interaction)
        else:
            view = ConfirmLeaveView(self.user_id, tx, self.back)
        await interaction.response.edit_message(view=view)


def get_limit_embed_and_view(user_id: int, back=None):
    tx = get_active_transaction(user_id)
    if tx:
        return MISSING, InvoiceLayoutView(user_id, tx, back)
    return MISSING, ShopLayoutView(user_id, back)
