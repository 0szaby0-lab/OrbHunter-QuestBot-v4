import os
import json
import asyncio
import time
import platform
import sys
from datetime import datetime
import discord
from discord.ext import commands
from discord import app_commands
from logger import log, Colors
import config
import session_msgs
from discord_client import DiscordAPI, fetch_latest_build_number
from completer import QuestAutocompleter, get_progress_bar
from quest_utils import (
    _get,
    get_user_status,
    get_quest_name,
    get_task_type,
    is_completed,
    is_expired,
    is_enrolled,
    get_seconds_needed,
    get_seconds_done
)

try:
    import psutil
except ImportError:
    psutil = None

bot_start_time = time.time()

# Persistence for channel configuration
CONFIG_FILE = "channel.json"

def load_channel_config():
    if not hasattr(config, 'SERVER_CHANNELS'):
        config.SERVER_CHANNELS = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                config.CHANNEL_ID = data.get("channel_id")
                config.SERVER_CHANNELS = data.get("server_channels", {})
                log(f"Működési csatorna konfiguráció betöltve: default={config.CHANNEL_ID}, szerverek={len(config.SERVER_CHANNELS)}", "ok")
        except Exception as e:
            log(f"Nem sikerült betölteni a csatorna konfigurációt: {e}", "warn")

def save_channel_config(channel_id, guild_id=None):
    try:
        if not hasattr(config, 'SERVER_CHANNELS'):
            config.SERVER_CHANNELS = {}
        if guild_id:
            config.SERVER_CHANNELS[str(guild_id)] = channel_id
            if not config.CHANNEL_ID:
                config.CHANNEL_ID = channel_id  # alapértelmezett, nem írja felül a többi szervert
        else:
            config.CHANNEL_ID = channel_id
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "channel_id": channel_id,
                "server_channels": config.SERVER_CHANNELS
            }, f, indent=2)
        log(f"Új működési csatorna konfiguráció mentve: {channel_id} (Szerver: {guild_id})", "ok")
    except Exception as e:
        log(f"Nem sikerült menteni a csatorna konfigurációt: {e}", "error")


def resolve_guild_channel_id(guild):
    """A szerver egyedi működési csatornája."""
    if not guild:
        return None
    cid = (getattr(config, "SERVER_CHANNELS", None) or {}).get(str(guild.id))
    if cid:
        return int(cid)
    if config.CHANNEL_ID and guild.get_channel(int(config.CHANNEL_ID)):
        return int(config.CHANNEL_ID)
    return None


def is_panel_channel(interaction: discord.Interaction) -> bool:
    """Az indítás gomb csak a szerver kijelölt csatornájában működik."""
    if not interaction.guild:
        return False
    return resolve_guild_channel_id(interaction.guild) == interaction.channel_id


def panel_mention_for_user(user_id: int, fallback: str = "fő vezérlőpult") -> str:
    """Megfelelő csatorna azonosítása a felhasználó számára."""
    info = config.ACTIVE_USERS.get(user_id) or {}
    candidates = []
    if info.get("guild_id"):
        g = bot.get_guild(info["guild_id"])
        if g:
            candidates.append(g)
    candidates += [g for g in bot.guilds if g.get_member(user_id)]
    for g in candidates:
        cid = resolve_guild_channel_id(g)
        if cid:
            return f"<#{cid}>"
    return fallback


async def send_extension_guide(interaction: discord.Interaction):
    """Részletes használati útmutató küldése."""
    from txa_layout import ExtensionGuideView
    view = ExtensionGuideView(interaction.user.id)
    if not interaction.response.is_done():
        await interaction.response.send_message(view=view, ephemeral=True)
    else:
        await interaction.followup.send(view=view, ephemeral=True)


async def handle_claim_reward_response(
    res: dict,
    quest_name: str,
    interaction: discord.Interaction,
    btn: discord.ui.Button = None,
    view: discord.ui.View = None
):
    """Jutalom beváltási válasz kezelése a Discord API-ból."""
    from txa_layout import claim_result_view, QUEST_HOME_URL
    uid = interaction.user.id
    is_captcha = (
        res.get("error") == "captcha_required"
        or res.get("captcha") is True
        or "captcha" in str(res.get("details", "")).lower()
        or "captcha-required" in str(res.get("details", ""))
        or "captcha_key" in str(res.get("details", ""))
    )

    async def refresh_message():
        if view and interaction.message:
            try:
                await interaction.message.edit(view=view)
            except Exception:
                pass

    if is_captcha:
        # Átvételi gomb cseréje közvetlen Discord Quest hivatkozásra
        if btn and view:
            try:
                row = btn.row
                view.remove_item(btn)
                view.add_item(discord.ui.Button(
                    label="Átvétel Discordon", emoji="🎁", style=discord.ButtonStyle.link,
                    url=QUEST_HOME_URL, row=row
                ))
            except Exception:
                pass
        await interaction.followup.send(view=claim_result_view(uid, "captcha", quest_name), ephemeral=True)
        await refresh_message()
        return

    if "error" in res:
        detail = str(res.get("details", res["error"]))
        await interaction.followup.send(view=claim_result_view(uid, "error", quest_name, detail=detail), ephemeral=True)
        return

    code = res.get("code")
    if btn:
        btn.disabled = True
        btn.label = f"Átvéve: {quest_name[:14]}"
        btn.style = discord.ButtonStyle.secondary
    await interaction.followup.send(view=claim_result_view(uid, "success", quest_name, code=code), ephemeral=True)
    await refresh_message()


class DMProgressView(discord.ui.View):
    def __init__(self, completer=None, user_id=None, status_callback=None):
        super().__init__(timeout=None)
        self.completer = completer
        self.user_id = user_id
        self.status_callback = status_callback

    @discord.ui.button(label="Leállítás", style=discord.ButtonStyle.danger, emoji="🛑", custom_id="btn_stop_quest", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.completer:
            await self.completer.stop()
            config.ACTIVE_USERS.pop(self.user_id, None)
            if self.completer.status_callback:
                await self.completer.status_callback("🛑 Folyamat leállítva.", finished=False)
            await interaction.followup.send("🛑 A küldetés munkameneted leállt.", ephemeral=True)

    @discord.ui.button(label="Frissítés", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="btn_refresh_quest", row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.status_callback:
            await self.status_callback("Frissítés folyamatban...")

    def update_claim_buttons(self, unclaimed_quests: dict):
        """Jutalom beváltási gombok automatikus hozzáadása a DM üzenethez."""
        current_claim_ids = {str(c.custom_id) for c in self.children if c.custom_id and c.custom_id.startswith("dm_claim_")}
        
        for qid, qname in unclaimed_quests.items():
            btn_id = f"dm_claim_{qid}"
            if btn_id not in current_claim_ids and len(self.children) < 25:
                row_idx = 1 if len([c for c in self.children if getattr(c, 'row', 0) == 1]) < 5 else 2
                btn = discord.ui.Button(
                    label=f"Átvétel: {qname[:14]}",
                    style=discord.ButtonStyle.success,
                    emoji="🎁",
                    custom_id=btn_id,
                    row=row_idx
                )
                btn.callback = self.make_claim_callback(qid, qname)
                self.add_item(btn)

    def make_claim_callback(self, quest_id, quest_name):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            if not self.completer:
                await interaction.followup.send(f"{config.EMOJI_FAIL} A munkamenet adatai nem találhatók.", ephemeral=True)
                return

            target_btn = None
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.custom_id == f"dm_claim_{quest_id}":
                    target_btn = child
                    break

            res = await self.completer.claim_reward(quest_id)
            await handle_claim_reward_response(res, quest_name, interaction, target_btn, self)
        return callback


class AdminSessionControlView(discord.ui.View):
    def __init__(self, target_user_id: int, completer=None, bot=None):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id
        self.completer = completer
        self.bot = bot

    @discord.ui.button(label="Munkamenet Leállítása", style=discord.ButtonStyle.danger, emoji="🛑", custom_id=None)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in config.ADMIN_IDS:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Nincs adminisztrátori jogosultságod.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        info = config.ACTIVE_USERS.pop(self.target_user_id, None)
        if self.completer:
            await self.completer.stop()
        if info and info.get('task'):
            info['task'].cancel()

        button.disabled = True
        button.label = "Munkamenet Leállítva"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        try:
            user = self.bot.get_user(self.target_user_id) or await self.bot.fetch_user(self.target_user_id)
            if user:
                await user.send("🛑 **A küldetés munkamenetedet egy adminisztrátor leállította.**")
        except Exception:
            pass

        if self.bot:
            await self.bot.update_control_panel()

        await interaction.followup.send(f"🛑 **A(z) <@{self.target_user_id}> felhasználó munkamenete leállítva!**", ephemeral=True)


async def notify_admins_session_started(bot, user_id, discord_user, quest_username, quest_user_id, start_ts, completer=None):
    """Értesítés (DM) küldése az adminoknak az új küldetés indításakor."""
    if not config.ADMIN_IDS:
        return []

    from limits_manager import get_user_quota_info
    quota = get_user_quota_info(user_id)
    
    embed = discord.Embed(
        title=f"⚡ [ADMIN MONITOR] Quest Munkamenet: {quest_username}",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=discord.utils.utcnow()
    )
    if hasattr(discord_user, "display_avatar") and discord_user.display_avatar:
        embed.set_author(name=f"Fiók: {quest_username} (Tag: {getattr(discord_user, 'name', 'Unknown')})", icon_url=discord_user.display_avatar.url)
    else:
        embed.set_author(name=f"Fiók: {quest_username}")

    embed.add_field(
        name="👤 Felhasználó Adatai",
        value=(
            f"• **Discord:** <@{user_id}> (`{getattr(discord_user, 'name', 'Unknown')}`, ID: `{user_id}`)\n"
            f"• **Quest ID:** `{quest_user_id}`\n"
            f"• **Elérhető egyenleg:** `💎 {quota.get('quota_str', 'N/A')}`"
        ),
        inline=False
    )
    embed.add_field(
        name="📊 Munkamenet Állapota",
        value=(
            f"• **Állapot:** `🚀 Csatlakozás folyamatban...`\n"
            f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
            f"• **Összesített haladás:** `▱▱▱▱▱▱▱▱▱▱` **0%**\n"
            f"• **Párhuzamos munkamenetek:** `{len(config.ACTIVE_USERS):02d} aktív folyamat`"
        ),
        inline=False
    )
    embed.set_footer(text="🔄 Élő Admin Monitor • Valós idejű telemetria • Quest Bot")

    records = []
    for admin_id in config.ADMIN_IDS:
        try:
            admin_user = bot.get_user(admin_id)
            if not admin_user:
                admin_user = await bot.fetch_user(admin_id)
            if admin_user:
                view = AdminSessionControlView(target_user_id=user_id, completer=completer, bot=bot)
                msg = session_msgs.track(await admin_user.send(embed=embed, view=view))
                records.append((msg, view))
        except Exception as e:
            log(f"Nem sikerült DM értesítést küldeni a(z) {admin_id} adminnak: {e}", "warn")

    return records


ACTIVE_QR_HANDLERS = {}


class QRLoginView(discord.ui.View):
    def __init__(self, auth_handler, user_id: int):
        super().__init__(timeout=130)
        self.auth_handler = auth_handler
        self.user_id = user_id

    @discord.ui.button(label="Mégse", style=discord.ButtonStyle.danger, emoji="✖️", row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ez a gomb nem neked szól.", ephemeral=True)
            return
        if self.auth_handler:
            self.auth_handler.cancel()
        ACTIVE_QR_HANDLERS.pop(self.user_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ **A bejelentkezési folyamat megszakítva.**", embed=None, view=self, attachments=[])


async def handle_qr_start(interaction: discord.Interaction):
    user_id = interaction.user.id

    from limits_manager import is_user_limited
    if is_user_limited(user_id):
        from payment_views import get_limit_embed_and_view, register_invoice
        embed, view = get_limit_embed_and_view(user_id)
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        register_invoice(interaction, view)
        return

    if user_id in config.ACTIVE_USERS:
        msg = (
            "🔄 **Ehhez a fiókhoz már fut egy aktív munkamenet!**\n"
            "Újraindításhoz először állítsd le a folyamatot a `/stop` paranccsal vagy a piros gombbal a privát üzeneteidben (DM)."
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return

    if user_id in ACTIVE_QR_HANDLERS:
        try:
            ACTIVE_QR_HANDLERS[user_id].cancel()
        except Exception:
            pass
        ACTIVE_QR_HANDLERS.pop(user_id, None)

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    qr_msg = None
    from remote_auth import RemoteAuthHandler
    import io

    async def on_qr_code(qr_bytes: bytes, qr_url: str):
        nonlocal qr_msg, auth_handler
        file = discord.File(io.BytesIO(qr_bytes), filename="discord_qr.png")
        embed = discord.Embed(
            title="📱 Discord Mobil Bejelentkezés",
            description=(
                "### ⚡ Automatikus Quest Indítás QR-Kóddal\n"
                "**Nem szükséges tokent megadnod!** Csak olvasd be a lenti kódot a telefonoddal:\n\n"
                "1️⃣ Nyisd meg a **telefonos Discord appot**.\n"
                "2️⃣ Lépj a profilodra ➔ **Beállítások ➔ QR-kód beolvasása** menübe.\n"
                "3️⃣ Irányítsd a kamerát az alábbi QR-kódra, majd koppints az **\"Igen, bejelentkezem\"** gombra!\n\n"
                "-# ⏱️ *A QR-kód 2 percig érvényes • Hivatalos Discord Remote Auth (RSA-2048)*"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.set_image(url="attachment://discord_qr.png")
        embed.set_footer(text="Discord Remote Auth v2 • Zéró manuális token")

        view = QRLoginView(auth_handler, user_id)
        qr_msg = await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)

    async def on_scanned(user_info: dict):
        nonlocal qr_msg, auth_handler
        if not qr_msg:
            return
        username = user_info.get("username", "Felhasználó")
        embed = discord.Embed(
            title="📱 QR-Kód Beolvasva!",
            description=(
                f"👤 **Észlelt fiók:** `{username}`\n\n"
                "👉 **Kérjük, koppints az 'Igen, bejelentkezem' gombra a telefonodon a jóváhagyáshoz!**"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Várakozás a telefonos megerősítésre...")
        view = QRLoginView(auth_handler, user_id)
        try:
            await qr_msg.edit(embed=embed, view=view, attachments=[])
        except Exception:
            pass

    async def on_token(token: str):
        nonlocal qr_msg
        ACTIVE_QR_HANDLERS.pop(user_id, None)
        embed = discord.Embed(
            title="✅ Sikeres Bejelentkezés!",
            description=(
                "🚀 **A hitelesítés sikeres!**\n\n"
                "A küldetések automatikus teljesítése elindult a háttérben.\n"
                "📬 A részletes folyamatjelzőt megnyitottuk a privát üzeneteidben (DM)!"
            ),
            color=discord.Color.green()
        )
        try:
            if qr_msg:
                await qr_msg.edit(embed=embed, view=None, attachments=[])
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            pass

        await start_quest_session(interaction, token, bot=interaction.client)

    async def on_cancel():
        nonlocal qr_msg
        ACTIVE_QR_HANDLERS.pop(user_id, None)
        embed = discord.Embed(
            title="❌ Bejelentkezés Megszakítva",
            description="A bejelentkezést elutasítottad vagy megszakítottad a telefonodon.",
            color=discord.Color.red()
        )
        try:
            if qr_msg:
                await qr_msg.edit(embed=embed, view=None, attachments=[])
        except Exception:
            pass

    async def on_error(err_msg: str):
        nonlocal qr_msg
        ACTIVE_QR_HANDLERS.pop(user_id, None)
        embed = discord.Embed(
            title="⚠️ Hitelesítési Időtúllépés",
            description=f"{err_msg}\n\nKérjük, kattints újra az **Indítás** gombra egy új kód kéréséhez.",
            color=discord.Color.red()
        )
        try:
            if qr_msg:
                await qr_msg.edit(embed=embed, view=None, attachments=[])
        except Exception:
            pass

    auth_handler = RemoteAuthHandler(
        on_qr_code=on_qr_code,
        on_scanned=on_scanned,
        on_token=on_token,
        on_cancel=on_cancel,
        on_error=on_error
    )
    ACTIVE_QR_HANDLERS[user_id] = auth_handler
    asyncio.create_task(auth_handler.run(timeout_seconds=120))



async def start_quest_session(interaction: discord.Interaction, user_token: str, bot=None):
    if bot is None:
        bot = interaction.client
    completer = None
    user_id = interaction.user.id
    username = interaction.user.name

    # Küldetéskvóta ellenőrzése a folytatás előtt
    from limits_manager import is_user_limited
    if is_user_limited(user_id):
        from payment_views import get_limit_embed_and_view, register_invoice
        embed, view = get_limit_embed_and_view(user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        register_invoice(interaction, view)
        return

    # Validate token
    api = DiscordAPI(user_token, getattr(config, 'BUILD_NUMBER', 504649))
    user_info = await api.validate_token()
    if not user_info:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} **Érvénytelen token vagy nem sikerült csatlakozni a Discordhoz!**\n"
            "Kérjük ellenőrizd a tokenedet, vagy kérj le egy újat az útmutató szerint.",
            ephemeral=True
        )
        return

    # Check if already running for this user
    if user_id in config.ACTIVE_USERS:
        await interaction.followup.send("🔄 **Ehhez a fiókhoz már fut egy munkamenet!** Újraindítás folyamatban...", ephemeral=True)
        try:
            # Stop existing completer
            old_info = config.ACTIVE_USERS[user_id]
            await old_info['completer'].stop()
            if old_info['task']:
                old_info['task'].cancel()
        except Exception as e:
            log(f"Hiba a korábbi szál leállításakor: {e}", "warn")
        await asyncio.sleep(1)

    # Open DM Channel with the user
    try:
        dm_channel = await interaction.user.create_dm()
    except Exception as e:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} **Nem sikerült privát üzenetet küldeni!**\n"
            "Kérjük, engedélyezd a közvetlen üzeneteket (DM) a szervertagoktól a folyamatjelző fogadásához.",
            ephemeral=True
        )
        return

    # Initialize view
    view = DMProgressView(user_id=user_id)

    # Send initial progress embed in DM
    start_ts = int(time.time())
    embed = discord.Embed(
        title="⚡ DISCORD AUTO-QUEST FOLYAMAT",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=f"Fiók: {user_info.get('username', username)}", icon_url=interaction.user.display_avatar.url)
    embed.add_field(
        name="📊 Áttekintés & Állapot",
        value=(
            f"• **Állapot:** `🚀 Csatlakozás folyamatban...`\n"
            f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
            f"• **Munkamenet haladás:** `▱▱▱▱▱▱▱▱▱▱` **0%**\n"
            f"• **Folyamat:** Küldetések felmérése a fiókodból..."
        ),
        inline=False
    )
    embed.set_footer(text="🔄 Automatikus frissítés • Quest Bot v4.0")
    try:
        dm_msg = session_msgs.track(await dm_channel.send(embed=embed, view=view))
    except Exception as e:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} **Nem sikerült privát üzenetet küldeni!** Kérjük, engedélyezd a közvetlen üzeneteket (DM) az adatvédelmi beállításokban.",
            ephemeral=True
        )
        return

    # Admin DM messages for real-time monitoring
    admin_records = []

    async def update_admin_monitors(embed_to_send, is_ended=False, ended_label="Munkamenet Befejeződött"):
        if not admin_records:
            return
        for a_msg, a_view in list(admin_records):
            try:
                if is_ended and a_view:
                    for btn in a_view.children:
                        if isinstance(btn, discord.ui.Button):
                            btn.disabled = True
                            btn.label = ended_label
                await a_msg.edit(embed=embed_to_send, view=a_view)
            except Exception:
                pass

    # Define status callback to edit the DM message
    async def status_callback(status_text: str, finished: bool = False, update_panel: bool = False):
        try:
            nonlocal dm_msg, view, admin_records
            
            if update_panel:
                await bot.update_control_panel()
            
            if status_text == "LIMIT_REACHED":
                from payment_views import get_limit_embed_and_view
                _, view_edit = get_limit_embed_and_view(user_id)
                await dm_msg.edit(embed=discord.Embed(
                    title="⚠️ Napi Küldetéskvóta Elérve",
                    color=discord.Color.orange(),
                    description="A folyamat felfüggesztve. Vásárolj további lehetőségeket az alábbi üzenetben a folytatáshoz."
                ), view=None)
                session_msgs.track(await dm_channel.send(view=view_edit))

                # Admin DM valós idejű frissítése limit elérésekor
                limit_admin_embed = discord.Embed(
                    title=f"⚠️ [LIMIT ELÉRVE] Munkamenet: {user_info.get('username', username)}",
                    color=discord.Color.orange(),
                    description=(
                        f"A(z) <@{user_id}> felhasználó elérte a mai küldetéslimitjét.\n"
                        f"A folyamat szünetel, amíg a felhasználó további lehetőségeket nem vásárol."
                    ),
                    timestamp=discord.utils.utcnow()
                )
                limit_admin_embed.set_footer(text="🔄 Live Admin Monitor • TXA Quest Bot")
                await update_admin_monitors(limit_admin_embed)
                return
            
            quests = []
            try:
                if completer:
                    raw_quests = getattr(completer, "last_quests", None) or await completer.fetch_quests()
                    quests = [
                        q for q in raw_quests 
                        if (is_completed(q) or not is_expired(q)) 
                        and q.get("id") not in completer.failed_404_ids
                    ]
            except Exception:
                pass
            
            # Auto-detect finished if all quests are completed
            if quests and all(is_completed(q) for q in quests):
                finished = True

            from limits_manager import get_user_total_completed, get_user_quota_info
            from system_info import make_bar
            user_total = get_user_total_completed(user_id)
            quota_info = get_user_quota_info(user_id)
            user_avatar_url = interaction.user.display_avatar.url
            user_display_name = user_info.get('username', username)
            
            completed_count = sum(1 for q in quests if is_completed(q)) if quests else 0
            total_count = len(quests) if quests else 0

            # Át nem vett küldetések lekérése
            unclaimed_quests = {}
            for q in quests:
                qid = q.get("id")
                us = get_user_status(q)
                claimed = bool(_get(us, "claimedAt", "claimed_at"))
                if is_completed(q) and not claimed:
                    unclaimed_quests[qid] = get_quest_name(q)

            # Jutalomátvételi gombok automatikus frissítése a View-ban
            view.update_claim_buttons(unclaimed_quests)

            if finished:
                color = discord.Color.green()
                for item in view.children:
                    if isinstance(item, discord.ui.Button) and item.custom_id == "btn_stop_quest":
                        item.disabled = True

                embed_edit = discord.Embed(
                    title="🎉 MINDEN KÜLDETÉS BEFEJEZŐDÖTT!",
                    color=color,
                    timestamp=discord.utils.utcnow()
                )
                embed_edit.set_author(name=f"Fiók: {user_display_name}", icon_url=user_avatar_url)

                embed_edit.add_field(
                    name="📊 Munkamenet Statisztika",
                    value=(
                        f"• **Állapot:** `🟢 100%-ban Befejezve`\n"
                        f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
                        f"• **Befejezett küldetések:** `✅ {completed_count}/{total_count} küldetés`\n"
                        f"• **Elérhető egyenleg:** `💎 {quota_info['quota_str']}`\n"
                        f"• **Összesített teljesítés:** `🏆 {user_total} küldetés`"
                    ),
                    inline=False
                )

                if unclaimed_quests:
                    embed_edit.add_field(
                        name="🎁 Átvételre Váró Jutalmak",
                        value="Kattints az alábbi **Jutalom Átvétele** gombokra a promóciós kódok megszerzéséhez vagy az aktiváláshoz!",
                        inline=False
                    )
                else:
                    embed_edit.add_field(
                        name="✨ Jutalmak",
                        value="Minden megszerzett ajándék elérhető a Discord **Quest / Gift Inventory (Ajándéktár)** menüjében.",
                        inline=False
                    )

                embed_edit.set_footer(text=f"Befejezve ekkor: {datetime.now().strftime('%H:%M:%S')} • Quest Bot v4.0")
                try:
                    await dm_msg.edit(embed=embed_edit, view=view)
                except discord.NotFound:
                    dm_msg = session_msgs.track(await dm_channel.send(embed=embed_edit, view=view))

                # Admin DM frissítése 100% elérésekor
                admin_finish_embed = discord.Embed(
                    title=f"🎉 [100% BEFEJEZVE] Munkamenet: {user_display_name}",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                admin_finish_embed.set_author(name=f"Fiók: {user_display_name} (Tag: {getattr(interaction.user, 'name', 'Unknown')})", icon_url=user_avatar_url)
                admin_finish_embed.add_field(
                    name="👤 Felhasználó Adatai",
                    value=f"• Tag: <@{user_id}>\n• Username: **{getattr(interaction.user, 'name', 'Unknown')}**\n• ID: `{user_id}`",
                    inline=False
                )
                admin_finish_embed.add_field(
                    name="📊 Munkamenet Eredménye",
                    value=(
                        f"• **Állapot:** `🟢 100%-ban Befejezve`\n"
                        f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
                        f"• **Befejezett küldetések:** `✅ {completed_count:02d}/{total_count:02d} küldetés`\n"
                        f"• **Összesített teljesítés:** `🏆 {user_total:02d} küldetés`"
                    ),
                    inline=False
                )
                admin_finish_embed.set_footer(text=f"🔄 Élő Admin Monitor • Befejezve: {datetime.now().strftime('%H:%M:%S')} • Quest Bot")
                await update_admin_monitors(admin_finish_embed, is_ended=True, ended_label="100%-ban Befejezve")

                # Befejezési értesítés küldése (1 perc után automatikusan törlődik)
                if completer and not getattr(completer, '_completion_notice_sent', False):
                    completer._completion_notice_sent = True
                    async def send_and_delete_notice():
                        try:
                            notice_msg = await dm_channel.send(
                                f"🔔 <@{user_id}> **Az összes Discord küldetésed sikeresen befejeződött!** 🎉\n"
                                f"*(Ez az értesítés 1 perc múlva automatikusan törlődik)*"
                            )
                            await asyncio.sleep(60)
                            await notice_msg.delete()
                        except Exception as notice_err:
                            log(f"Hiba a befejezési értesítés küldésekor/törlésekor: {notice_err}", "warn")
                    asyncio.create_task(send_and_delete_notice())

                return

            elif completer and completer.stop_event.is_set():
                status_badge = "🛑 Folyamat Leállítva"
                color = discord.Color.red()
                for item in view.children:
                    if isinstance(item, discord.ui.Button) and item.custom_id == "btn_stop_quest":
                        item.disabled = True
            else:
                status_badge = "🚀 Folyamatban"
                color = discord.Color.from_rgb(88, 101, 242)

            overall_ratio = (completed_count / total_count * 100) if total_count > 0 else 0
            overall_bar = make_bar(overall_ratio, length=10)

            embed_edit = discord.Embed(
                title="⚡ DISCORD AUTO-QUEST FOLYAMAT",
                color=color,
                timestamp=discord.utils.utcnow()
            )
            embed_edit.set_author(name=f"Fiók: {user_display_name}", icon_url=user_avatar_url)

            # 1. blokk: Áttekintés
            embed_edit.add_field(
                name="📊 Áttekintés & Állapot",
                value=(
                    f"• **Állapot:** `{status_badge}`\n"
                    f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
                    f"• **Munkamenet haladás:** `{overall_bar}` **{overall_ratio:.0f}%** ({completed_count:02d}/{total_count:02d} kész)\n"
                    f"• **Elérhető egyenleg:** `💎 {quota_info['quota_str']}`\n"
                    f"• **Összesített teljesítés:** `🏆 {user_total:02d} küldetés`"
                ),
                inline=False
            )

            # 2. blokk: Aktív feladat
            active_q = None
            if completer and completer.current_quest_id and quests:
                for q in quests:
                    if q.get("id") == completer.current_quest_id:
                        active_q = q
                        break

            if active_q:
                active_name = get_quest_name(active_q)
                active_task = get_task_type(active_q) or "?"
                task_label = {
                    "WATCH_VIDEO": "🎬 Videónézés",
                    "PLAY_ON_DESKTOP": "🖥️ Játék PC-n (Desktop)",
                    "STREAM_ON_DESKTOP": "📡 Stream Game Desktop",
                    "PLAY_ACTIVITY": "🕹️ Discord Aktivitás Játék"
                }.get(active_task, f"🎮 {active_task}")

                needed = get_seconds_needed(active_q)
                done = get_seconds_done(active_q)
                if hasattr(completer, "current_quest_info") and completer.current_quest_info:
                    done = max(done, completer.current_quest_info.get("done", done))
                    needed = max(needed, completer.current_quest_info.get("needed", needed))

                pct = (done / needed * 100) if needed > 0 else 0
                quest_bar = make_bar(pct, length=10)
                remaining = max(0, int(needed - done))
                eta_text = f"{remaining // 60:02d}p {remaining % 60:02d}s" if remaining >= 60 else f"{remaining:02d}s"

                embed_edit.add_field(
                    name="⚡ Jelenleg Feldolgozás Alatt",
                    value=(
                        f"• **Küldetés:** **{active_name}**\n"
                        f"• **Feladattípus:** `{task_label}`\n"
                        f"• **Haladás:** `{quest_bar}` **{pct:.0f}%** (`{int(done):02d}s / {int(needed):02d}s`)\n"
                        f"• **Hátralévő idő:** `⏳ ~{eta_text}`"
                    ),
                    inline=False
                )

            # 3. blokk: Küldetések listája
            if quests:
                quest_lines = []
                for q in quests:
                    q_name = get_quest_name(q)
                    q_task = get_task_type(q) or "?"
                    q_id = q.get("id")
                    us = get_user_status(q)
                    claimed = bool(_get(us, "claimedAt", "claimed_at"))

                    if is_completed(q):
                        if claimed:
                            quest_lines.append(f"{config.EMOJI_SUCCESS} ~~{q_name}~~ `(Átvéve)`")
                        else:
                            quest_lines.append(f"🎁 **{q_name}** `[Átvételre vár]`")
                    elif completer and completer.current_quest_id == q_id:
                        quest_lines.append(f"🔄 **{q_name}** `[Folyamatban...]`")
                    elif is_enrolled(q):
                        quest_lines.append(f"⏳ {q_name} `[{q_task}]` *(Várakozik)*")
                    else:
                        quest_lines.append(f"⚪ {q_name} `[{q_task}]` *(Még nincs elfogadva)*")

                embed_edit.add_field(
                    name=f"📋 Küldetések Listája ({completed_count:02d}/{total_count:02d})",
                    value="\n".join(quest_lines[:12]),
                    inline=False
                )
            else:
                embed_edit.add_field(name="📋 Küldetések Listája", value=status_text, inline=False)

            embed_edit.set_footer(text=f"🔄 Automatikus frissítés • {datetime.now().strftime('%H:%M:%S')} • Quest Bot v4.0")
            try:
                await dm_msg.edit(embed=embed_edit, view=view)
            except discord.NotFound:
                dm_msg = session_msgs.track(await dm_channel.send(embed=embed_edit, view=view))

            # Valós idejű admin DM frissítés
            is_stopped = bool(completer and completer.stop_event.is_set())
            admin_status_title = f"🛑 [LEÁLLÍTVA] Munkamenet: {user_display_name}" if is_stopped else f"⚡ [ÉLŐ MONITOR] Auto-Quest: {user_display_name}"
            admin_status_color = discord.Color.red() if is_stopped else color

            admin_embed = discord.Embed(
                title=admin_status_title,
                color=admin_status_color,
                timestamp=discord.utils.utcnow()
            )
            admin_embed.set_author(name=f"Fiók: {user_display_name} (Tag: {getattr(interaction.user, 'name', 'Unknown')})", icon_url=user_avatar_url)
            admin_embed.add_field(
                name="👤 Felhasználó Adatai",
                value=(
                    f"• **Discord:** <@{user_id}> (`{getattr(interaction.user, 'name', 'Unknown')}`, ID: `{user_id}`)\n"
                    f"• **Quest ID:** `{user_info.get('id', 'N/A')}`\n"
                    f"• **Elérhető egyenleg:** `💎 {quota_info['quota_str']}`"
                ),
                inline=False
            )
            admin_embed.add_field(
                name="📊 Valós Idejű Haladás",
                value=(
                    f"• **Állapot:** `{status_badge}`\n"
                    f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
                    f"• **Összesített haladás:** `{overall_bar}` **{overall_ratio:.0f}%** ({completed_count:02d}/{total_count:02d} xong)\n"
                    f"• **Párhuzamos munkamenetek:** `{len(config.ACTIVE_USERS):02d} aktív folyamat`"
                ),
                inline=False
            )

            if active_q:
                admin_embed.add_field(
                    name="⚡ Jelenleg Feldolgozás Alatt",
                    value=(
                        f"• **Küldetés:** **{active_name}**\n"
                        f"• **Típus:** `{task_label}`\n"
                        f"• **Haladás:** `{quest_bar}` **{pct:.0f}%** (`{done:.0f}s / {needed}s`)\n"
                        f"• **Hátralévő idő:** `⏳ ~{eta_text}`"
                    ),
                    inline=False
                )

            if quests:
                admin_embed.add_field(
                    name=f"📋 Küldetések Listája ({completed_count:02d}/{total_count:02d})",
                    value="\n".join(quest_lines[:8]),
                    inline=False
                )

            admin_embed.set_footer(text=f"🔄 Live Admin Monitor • {datetime.now().strftime('%H:%M:%S')} • TXA Quest Bot v4.0")
            await update_admin_monitors(admin_embed, is_ended=is_stopped, ended_label="Munkamenet Leállítva")
        except Exception as e:
            log(f"Nem sikerült frissíteni a DM-et a(z) {user_id} felhasználónak: {e}", "warn")

    # Initialize completer
    completer = QuestAutocompleter(api, user_id, status_callback=status_callback)
    view.completer = completer
    view.status_callback = status_callback

    # Indítási értesítés (DM) küldése az adminnak valós idejű monitorozáshoz
    admin_records = await notify_admins_session_started(
        bot=bot,
        user_id=user_id,
        discord_user=interaction.user,
        quest_username=user_info.get('username', username),
        quest_user_id=user_info.get('id', 'N/A'),
        start_ts=start_ts,
        completer=completer
    )

    # Define worker that guarantees ACTIVE_USERS cleanup and panel update
    async def session_worker():
        try:
            await completer.run()
        finally:
            config.ACTIVE_USERS.pop(user_id, None)
            try:
                await bot.update_control_panel()
            except Exception:
                pass

    # Start in background task
    task = asyncio.create_task(session_worker())
    
    # Store metadata
    config.ACTIVE_USERS[user_id] = {
        'completer': completer,
        'task': task,
        'username': user_info.get('username', username),
        'start_timestamp': start_ts,
        'discord_user': interaction.user,
        'guild_id': interaction.guild_id,
        'admin_records': admin_records
    }

    # Ephemeral notification
    await interaction.followup.send(
        f"{config.EMOJI_SUCCESS} **Sikeres bejelentkezés: `{user_info.get('username')}`!**\n"
        f"📬 A részletes folyamatjelző a privát üzeneteid (DM) közé érkezik.",
        ephemeral=True
    )

    # Update main control panel stats
    await bot.update_control_panel()


TASK_LABELS = {
    "WATCH_VIDEO": "🎬 Videónézés",
    "WATCH_VIDEO_ON_MOBILE": "📱 Videónézés Mobilon",
    "PLAY_ON_DESKTOP": "🖥️ Játék PC-n (Desktop)",
    "STREAM_ON_DESKTOP": "📡 Képernyőmegosztás (Stream)",
    "PLAY_ACTIVITY": "🕹️ Discord Aktivitás Játék"
}


class SessionDetailsView(discord.ui.View):
    def __init__(self, bot_instance, user_id, completer, unclaimed_quests=None):
        super().__init__(timeout=180)
        self.bot_instance = bot_instance
        self.user_id = user_id
        self.completer = completer
        self.unclaimed_quests = unclaimed_quests or {}

        # Row 0: Jutalomátvételi gombok (ha vannak)
        for qid, qname in list(self.unclaimed_quests.items())[:4]:
            btn = discord.ui.Button(
                label=f"Átvétel: {qname[:16]}",
                style=discord.ButtonStyle.success,
                emoji="🎁",
                custom_id=f"claim_det_{qid}_{int(time.time())}",
                row=0
            )
            btn.callback = self.make_claim_callback(qid, qname, btn)
            self.add_item(btn)

        # Row 1: Nút Frissítés & Nút Munkamenet Leállítása
        btn_refresh = discord.ui.Button(
            label="Frissítés",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            row=1
        )
        btn_refresh.callback = self.refresh_callback
        self.add_item(btn_refresh)

        btn_stop = discord.ui.Button(
            label="Munkamenet Leállítása",
            style=discord.ButtonStyle.danger,
            emoji="🛑",
            row=1
        )
        btn_stop.callback = self.stop_callback
        self.add_item(btn_stop)

    def make_claim_callback(self, qid, qname, btn):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb nem neked szól.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            if not self.completer:
                await interaction.followup.send(f"{config.EMOJI_FAIL} A munkamenet már befejeződött, nem lehet jutalmat átvenni.", ephemeral=True)
                return
            res = await self.completer.claim_reward(qid)
            await handle_claim_reward_response(res, qname, interaction, btn, self)
        return callback

    async def refresh_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Nincs jogosultságod a panel használatához.", ephemeral=True)
            return
        await interaction.response.defer()
        embed, view = await build_session_details_response(self.user_id, interaction.user, self.bot_instance)
        await interaction.edit_original_response(embed=embed, view=view)

    async def stop_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Nincs jogosultságod más felhasználó munkamenetének leállításához.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        info = config.ACTIVE_USERS.pop(self.user_id, None)
        if info:
            try:
                await info['completer'].stop()
                if info.get('task'):
                    info['task'].cancel()
            except Exception as e:
                log(f"Hiba a(z) {self.user_id} munkamenetének leállításakor: {e}", "warn")
            await self.bot_instance.update_control_panel()
            await interaction.followup.send("🛑 **A küldetés munkameneted sikeresen leállítva!**", ephemeral=True)
            for child in self.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        else:
            await interaction.followup.send("ℹ️ Ez a munkamenet már korábban befejeződött.", ephemeral=True)


class IdleDetailsView(discord.ui.View):
    def __init__(self, bot_instance, user_id):
        super().__init__(timeout=180)
        self.bot_instance = bot_instance
        self.user_id = user_id

    @discord.ui.button(label="Frissítés", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Nincs jogosultságod a panel használatához.", ephemeral=True)
            return
        await interaction.response.defer()
        embed, view = await build_session_details_response(self.user_id, interaction.user, self.bot_instance)
        await interaction.edit_original_response(embed=embed, view=view)

    @discord.ui.button(label="Egyenleg / Vásárlás", style=discord.ButtonStyle.success, emoji="💳", row=0)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb nem neked szól.", ephemeral=True)
            return
        from payment_views import get_limit_embed_and_view, register_invoice
        _, view = get_limit_embed_and_view(self.user_id)
        await interaction.response.send_message(view=view, ephemeral=True)
        register_invoice(interaction, view)


async def build_session_details_response(user_id: int, discord_user: discord.User, bot_instance):
    from limits_manager import get_user_quota_info, get_user_total_completed
    from system_info import make_bar

    quota = get_user_quota_info(user_id)
    user_total = get_user_total_completed(user_id)

    # 1. ESET: NINCS AKTÍV MUNKAMENET
    if user_id not in config.ACTIVE_USERS:
        channel_mention = panel_mention_for_user(user_id, "Fő vezérlőpult")
        embed = discord.Embed(
            title="📋 RÉSZLETES JELENTÉS & QUEST EGYENLEG",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"Felhasználó: {discord_user.name}", icon_url=discord_user.display_avatar.url)
        embed.set_thumbnail(url=discord_user.display_avatar.url)

        embed.add_field(
            name="📊 Fiók Állapota",
            value=(
                f"• **Állapot:** `💤 Jelenleg nincs aktív munkamenet`\n"
                f"• **Discord fiók:** <@{user_id}> (ID: `{user_id}`)\n"
                f"• **Működési csatorna:** {channel_mention}"
            ),
            inline=False
        )

        if quota['is_admin'] and getattr(config, "ADMIN_BYPASS_LIMIT", True):
            free_status = "♾️ Végtelen (Admin kvótamentesség)"
        else:
            free_status = f"{config.EMOJI_SUCCESS} 01 lehetőség elérhető (Ingyenes napi kvóta)" if quota['free_available'] else f"{config.EMOJI_FAIL} A mai ingyenes kvóta elfogyott (Reset 00:00-kor)"
        embed.add_field(
            name="💎 Kvóta & Quest Egyenleg",
            value=(
                f"• **Elérhető lehetőségek:** `💎 {quota['quota_str']}`\n"
                f"• **Mai ingyenes lehetőség:** `{free_status}`\n"
                f"• **Megvásárolt tartalék:** `{quota['purchased_balance']:02d} lehetőség`\n"
                f"• **Összes teljesített küldetés:** `🏆 {user_total:02d} küldetés`"
            ),
            inline=False
        )

        embed.add_field(
            name="🚀 Útmutató az Indításhoz és Jutalmakhoz",
            value=(
                f"1️⃣ Kattints a **🚀 Indítás** gombra a(z) {channel_mention} csatornában és add meg a Discord tokenedet.\n"
                f"2️⃣ A bot automatikusan beolvassa, elfogadja és 100%-ban teljesíti a feladatokat a háttérben.\n"
                f"3️⃣ Az ajándékkódok vagy profildíszek azonnal átvehetők a befejezés után.\n"
                f"💡 *Kattints a **💳 Egyenleg / Vásárlás** gombra alább, ha további lehetőségeket szeretnél vásárolni.*"
            ),
            inline=False
        )
        embed.set_footer(text="Discord Quest Bot • Automatikus frissítés")
        view = IdleDetailsView(bot_instance, user_id)
        return embed, view

    # 2. ESET: AKTÍV MUNKAMENET FOLYAMATBAN
    info = config.ACTIVE_USERS[user_id]
    completer = info.get('completer')
    start_ts = info.get('start_timestamp', int(time.time()))
    acc_username = info.get('username', discord_user.name)

    raw_quests = getattr(completer, "last_quests", None) or []
    if not raw_quests and completer:
        try:
            raw_quests = await completer.fetch_quests()
        except Exception:
            raw_quests = []

    active_quests = [
        q for q in raw_quests
        if (is_completed(q) or not is_expired(q)) and q.get("id") not in getattr(completer, "failed_404_ids", set())
    ] if raw_quests else []

    completed_quests = [q for q in active_quests if is_completed(q)]
    completed_count = len(completed_quests)
    total_count = len(active_quests)
    overall_ratio = (completed_count / total_count * 100) if total_count > 0 else 0
    overall_bar = make_bar(overall_ratio, length=12)

    is_all_finished = (completed_count == total_count and total_count > 0)
    color = discord.Color.green() if is_all_finished else discord.Color.from_rgb(88, 101, 242)
    status_badge = "🟢 100%-ban Befejezve" if is_all_finished else "🚀 Folyamatban lévő Automata Teljesítés"

    embed = discord.Embed(
        title="⚡ VALÓS IDEJŰ FOLYAMATJELENTÉS",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=f"Discord Fiók: {acc_username}", icon_url=discord_user.display_avatar.url)
    embed.set_thumbnail(url=discord_user.display_avatar.url)

    # 1. blokk: Munkamenet áttekintése
    embed.add_field(
        name="📊 Munkamenet Áttekintés",
        value=(
            f"• **Állapot:** `{status_badge}`\n"
            f"• **Kezdés ideje:** <t:{start_ts}:R> *(ekkor: <t:{start_ts}:T>)*\n"
            f"• **Befejezett feladatok:** `{overall_bar}` **{overall_ratio:.1f}%** (`{completed_count:02d}/{total_count:02d}` küldetés)\n"
            f"• **Elérhető lehetőségek:** `💎 {quota['quota_str']}`\n"
            f"• **Összesített teljesítés:** `🏆 {user_total:02d} küldetés`"
        ),
        inline=False
    )

    # 2. blokk: Aktuálisan futó küldetés (Kiemelt)
    active_q = None
    if completer and completer.current_quest_id and active_quests:
        for q in active_quests:
            if q.get("id") == completer.current_quest_id:
                active_q = q
                break

    if active_q and not is_all_finished:
        q_name = get_quest_name(active_q)
        q_task = get_task_type(active_q) or "?"
        task_label = TASK_LABELS.get(q_task, f"🎮 {q_task}")
        needed = get_seconds_needed(active_q)
        done = get_seconds_done(active_q)
        if hasattr(completer, "current_quest_info") and completer.current_quest_info:
            done = max(done, completer.current_quest_info.get("done", done))
            needed = max(needed, completer.current_quest_info.get("needed", needed))
        pct = (done / needed * 100) if needed > 0 else 0
        qbar = make_bar(pct, length=10)
        rem = max(0, int(needed - done))
        eta_str = f"{rem // 60:02d} perc {rem % 60:02d} mp" if rem >= 60 else f"{rem:02d} mp"

        embed.add_field(
            name="⚡ Jelenleg Feldolgozás Alatt",
            value=(
                f"• **Küldetés:** **{q_name}**\n"
                f"• **Típus:** `{task_label}`\n"
                f"• **Haladás:** `{qbar}` **{pct:.1f}%** (`{int(done):02d}s / {int(needed):02d}s`)\n"
                f"• **Hátralévő idő:** `⏳ ~{eta_str}`"
            ),
            inline=False
        )

    # 3. blokk: Részletes feladatlista
    unclaimed_quests = {}
    completed_lines = []
    pending_lines = []

    for q in active_quests:
        name = get_quest_name(q)
        task = get_task_type(q) or "?"
        task_str = TASK_LABELS.get(task, task)
        qid = q.get("id")
        us = get_user_status(q)
        claimed = bool(_get(us, "claimedAt", "claimed_at"))

        if is_completed(q):
            if claimed:
                completed_lines.append(f"{config.EMOJI_SUCCESS} ~~{name}~~ `(Jutalom átvéve)`")
            else:
                completed_lines.append(f"🎁 **{name}** `[Jutalom átvételre vár - Kattints az alábbi gombra]`")
                unclaimed_quests[qid] = name
        elif active_q and qid == active_q.get("id"):
            pass  # Már megjelenítve a kiemelt blokkban
        else:
            status_text = "Sorban áll" if is_enrolled(q) else "Még nincs elfogadva"
            pending_lines.append(f"⚪ **{name}** • `{task_str}` *({status_text})*")

    if completed_lines:
        embed.add_field(
            name=f"🟢 Befejezett Küldetések ({len(completed_lines):02d}/{total_count:02d} küldetés)",
            value="\n".join(completed_lines[:10]),
            inline=False
        )

    if pending_lines:
        embed.add_field(
            name=f"⏳ Várakozási Sor ({len(pending_lines):02d} küldetés)",
            value="\n".join(pending_lines[:10]),
            inline=False
        )

    if not active_quests:
        embed.add_field(
            name="📭 Küldetések Listája",
            value="*Küldetéslista betöltése a Discordról... Kattints a 🔄 Frissítés gombra néhány másodperc múlva.*",
            inline=False
        )

    embed.set_footer(text="🔄 Frissítés gomb az élő időhöz · 🛑 Munkamenet Leállítása · Quest Bot")
    view = SessionDetailsView(bot_instance, user_id, completer, unclaimed_quests)
    return embed, view


class ControlView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None) # Persistent view
        self.bot = bot
        if config.EMOJI_ROCKET:
            self.start_button.emoji = config.EMOJI_ROCKET

    @discord.ui.button(label="Indítás", style=discord.ButtonStyle.success, emoji="🚀", custom_id="btn_start")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_panel_channel(interaction):
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb ebben a csatornában nem használható.", ephemeral=True)
            return

        from limits_manager import is_user_limited
        if is_user_limited(interaction.user.id):
            from payment_views import get_limit_embed_and_view, register_invoice
            embed, view = get_limit_embed_and_view(interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            register_invoice(interaction, view)
            return
        
        await handle_qr_start(interaction)

    @discord.ui.button(label="Részletek", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="btn_details")
    async def details_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_panel_channel(interaction):
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb ebben a csatornában nem használható.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        embed, view = await build_session_details_response(interaction.user.id, interaction.user, self.bot)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Egyenleg", style=discord.ButtonStyle.primary, emoji="💎", custom_id="btn_quota")
    async def quota_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_panel_channel(interaction):
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb ebben a csatornában nem használható.", ephemeral=True)
            return

        from payment_views import get_limit_embed_and_view, register_invoice
        embed, view = get_limit_embed_and_view(interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        register_invoice(interaction, view)

    @discord.ui.button(label="Útmutató", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="btn_guide")
    async def guide_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_panel_channel(interaction):
            await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb ebben a csatornában nem használható.", ephemeral=True)
            return
        
        await send_extension_guide(interaction)


async def cleanup_all_users():
    log(f"{len(config.ACTIVE_USERS)} háttérben futó fiók leállítása...", "info")
    for uid, info in list(config.ACTIVE_USERS.items()):
        try:
            await info['completer'].stop()
            if info['task']:
                info['task'].cancel()
            log(f"  -> Sikeresen leállítva: {Colors.BOLD}{info['username']}{Colors.RESET}", "ok")
        except Exception as e:
            log(f"Hiba a(z) {uid} szál leállításakor: {e}", "error")
    await asyncio.sleep(0.5)
    try:
        await session_msgs.purge_tracked(bot)
    except Exception as e:
        log(f"Hiba a munkamenet üzenetek törlésekor: {e}", "warn")

class QuestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.control_msg_id = None
        self.persistent_views_added = False

    async def close(self):
        log("Aktív munkamenetek bezárása és takarítás...", "info")
        await cleanup_all_users()
        await super().close()
        log("A rendszer teljesen leállt. Viszlát!", "ok")

    async def setup_hook(self):
        # Register persistent view
        self.add_view(ControlView(self))
        log("Állandó vezérlő view sikeresen betöltve", "ok")

        from limits_manager import roll_discounts
        roll_discounts()
        from payment_views import payment_watcher
        self.loop.create_task(payment_watcher(self))
        log("Automatikus fizetésellenőrzés bekapcsolva", "ok")

        # Fetch build number
        config.BUILD_NUMBER = await fetch_latest_build_number()

        # Render.com / Cloud HTTP Server for health check
        from render_server import start_render_web_server
        self.loop.create_task(start_render_web_server(self, bot_start_time))

    async def on_ready(self):
        log(f"Bot sikeresen bejelentkezett: {Colors.BOLD}{self.user}{Colors.RESET}", "ok")
        
        try:
            import txa_layout
            txa_layout.configure(avatar=str(self.user.display_avatar.url), invite=get_bot_invite_url(self))
        except Exception as e:
            log(f"Nem sikerült beállítani az avatárt/meghívót a felülethez: {e}", "warn")

        from emoji_sync import sync_application_emojis
        await sync_application_emojis(self)

        # Korábbi futásból maradt munkamenet üzenetek törlése
        await session_msgs.purge_tracked(self)

        # Load saved channel config
        load_channel_config()

        # Update/Create Control Panel in channel
        await self.update_control_panel()

        # Csak globális parancsok szinkronizálása a duplikáció elkerülésére
        for guild in self.guilds:
            try:
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                log(f"Duplikált parancsok törölve ezen a szerveren: {guild.name}", "ok")
            except Exception as ge:
                log(f"Hiba a parancsok tisztításakor ({guild.id}): {ge}", "warn")
        try:
            synced = await self.tree.sync()
            log(f"{len(synced)} globális slash parancs sikeresen szinkronizálva.", "ok")
        except Exception as e:
            log(f"Hiba a slash parancsok szinkronizálásakor: {e}", "error")

    async def on_interaction(self, interaction: discord.Interaction):
        """Lejárt felületek kezelése újraindítás után."""
        if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
            return
        await asyncio.sleep(2.5)
        if interaction.response.is_done():
            return
        try:
            await interaction.response.send_message(
                "⌛ **Ez a vezérlőpult lejárt** (a bot újraindult vagy túl sok idő telt el).\n"
                "Kérjük, írd be újra a parancsot egy új felület megnyitásához.",
                ephemeral=True
            )
        except Exception:
            pass

    def pick_panel_channel(self, guild: discord.Guild):
        """A legmegfelelőbb csatorna kiválasztása a Vezérlőpanel számára.
        Nyilvános csatornák és rendszer csatornák előnyben részesítése."""
        me = guild.me
        keywords = ("quest", "auto", "bot", "nitro", "free", "parancs", "kuldetes", "command")
        best, best_score = None, -1
        for ch in guild.text_channels:
            ok, _ = check_bot_channel_permissions(ch, me)
            if not ok:
                continue
            everyone = ch.permissions_for(guild.default_role)
            score = 0
            name = ch.name.lower()
            if any(k in name for k in keywords):
                score += 5
            if everyone.view_channel:
                score += 3
            if everyone.send_messages:
                score += 2
            if guild.system_channel and ch.id == guild.system_channel.id:
                score += 1
            if score > best_score:
                best, best_score = ch, score
        return best

    async def on_guild_join(self, guild: discord.Guild):
        """Új szerverhez való csatlakozáskor a vezérlőpanel automatikus létrehozása."""
        log(f"🎉 A bot csatlakozott egy új szerverhez: {guild.name} (ID: {guild.id})", "ok")
        # A globális parancsok automatikusan érvényesek az új szerveren.
        target_channel = self.pick_panel_channel(guild)

        if target_channel:
            save_channel_config(target_channel.id, guild_id=guild.id)
            try:
                welcome_embed = discord.Embed(
                    title="🎉 Köszönjük, hogy hozzáadtad a Quest Botot!",
                    description=(
                        f"A bot készen áll a használatra a(z) **{guild.name}** szerveren!\n\n"
                        f"📍 **A Vezérlőpanel automatikusan kihelyezve ide:** {target_channel.mention}\n"
                        f"🚀 Kattints az **Indítás** gombra a vezérlőpanelen, vagy használd a `/help` parancsot a részletekért."
                    ),
                    color=discord.Color.green()
                )
                await target_channel.send(embed=welcome_embed)
                await self.update_control_panel(target_channel=target_channel)
                log(f"Vezérlőpanel automatikusan kihelyezve: #{target_channel.name} ({guild.name})", "ok")
            except Exception as e:
                log(f"Nem sikerült üdvözlő üzenetet küldeni ({guild.id}): {e}", "warn")
            return

        # Nincs megfelelő jog -> értesítés a szervertulajdonosnak
        log(f"Nem található megfelelő csatorna itt: {guild.name} (jogosultság hiánya)", "warn")
        try:
            owner = guild.owner or await self.fetch_user(guild.owner_id)
            if owner:
                await owner.send(
                    f"⚠️ **A Quest Bot csatlakozott a(z) `{guild.name}` szerverhez, de nem rendelkezik megfelelő jogosultsággal a Vezérlőpanel kihelyezéséhez.**\n"
                    f"Kérjük biztosítsd a következő jogosultságokat: **Csatorna megtekintése, Üzenetek küldése, Beágyazott hivatkozások, Üzenetelőzmények olvasása** "
                    f"legalább egy csatornában, vagy hívd meg újra a botot az alábbi linken:\n{get_bot_invite_url(self)}"
                )
        except Exception as e:
            log(f"Nem sikerült értesíteni a(z) {guild.id} szerver tulajdonosát: {e}", "warn")

    async def _resolve_channel(self, cid):
        ch = self.get_channel(cid)
        if ch:
            return ch
        try:
            return await self.fetch_channel(cid)
        except Exception as fetch_err:
            log(f"Nem sikerült elérni a csatornát (ID: {cid}): {fetch_err}", "warn")
            return None

    async def update_control_panel(self, target_channel=None):
        """Vezérlőpanel létrehozása/frissítése szerverenként.
        Csak a kijelölt csatorna frissítése."""
        from limits_manager import get_guild_completed
        channels = []
        if target_channel is not None:
            if hasattr(target_channel, "send"):
                channels.append(target_channel)
            elif str(target_channel).isdigit():
                ch = await self._resolve_channel(int(target_channel))
                if ch:
                    channels.append(ch)
        else:
            mapped = getattr(config, "SERVER_CHANNELS", None) or {}
            for cid in set(int(c) for c in mapped.values()):
                ch = await self._resolve_channel(cid)
                if ch:
                    channels.append(ch)
            # Alapértelmezett csatorna ha nincs egyéni
            if config.CHANNEL_ID and int(config.CHANNEL_ID) not in {int(c) for c in mapped.values()}:
                ch = await self._resolve_channel(int(config.CHANNEL_ID))
                if ch and str(getattr(getattr(ch, "guild", None), "id", "")) not in mapped:
                    channels.append(ch)

        if not channels:
            log("Nincs beállítva működési csatorna. Használd a /channel vagy /setup_here parancsot.", "warn")
            return

        for channel in channels:
            try:
                guild_id = getattr(getattr(channel, "guild", None), "id", None)
                active_count = sum(1 for i in config.ACTIVE_USERS.values() if i.get("guild_id") == guild_id)
                embed = discord.Embed(
                    title="🎯 Discord Quest Automata",
                    description=(
                        "Szerezz **Nitrót, avatar díszt, profil dekorációt...** kézi játék nélkül!\n"
                        "Kattints az 🚀 **Indítás** gombra a QR-kód beolvasásához — a rendszer automatikusan **beolvassa • elfogadja • teljesíti** a küldetéseket."
                    ),
                    color=discord.Color.blue()
                )
                embed.set_author(name="TXA")
                embed.add_field(name="👤 Munkamenetek ezen a szerveren", value=f"```\n{active_count:02d}\n```", inline=True)
                embed.add_field(name="⏳ Várakozási sor", value="```\n00\n```", inline=True)
                embed.add_field(name="▶️ Folyamatban", value=f"```\n{active_count:02d}\n```", inline=True)
                embed.add_field(name=f"{config.EMOJI_SUCCESS} Teljesített küldetések ezen a szerveren", value=f"```\n{get_guild_completed(guild_id):02d}\n```", inline=False)

                current_time = time.strftime("%H:%M %d/%m/%y")
                embed.set_footer(text=f"Auto Scan • Auto Accept • Auto Complete • {current_time} | Quest Bot v4.0 - Built by TXA")

                # Régi bot üzenetek törlése a csatornából és új panel küldése
                try:
                    async for msg in channel.history(limit=100):
                        if msg.author == self.user:
                            await msg.delete()
                except Exception as e:
                    log(f"Hiba a korábbi üzenetek törlésekor ({getattr(channel, 'id', 'unknown')}): {e}", "warn")

                try:
                    await channel.send(embed=embed, view=ControlView(self))
                except Exception as e:
                    log(f"Nem sikerült elküldeni a vezérlőpanelt ({getattr(channel, 'id', 'unknown')}): {e}", "error")
            except Exception as e:
                log(f"Hiba a csatorna kezelésekor ({getattr(channel, 'id', 'unknown')}): {e}", "error")


def check_bot_channel_permissions(channel, me):
    """Ellenőrzi, hogy a bot rendelkezik-e a szükséges jogosultságokkal a csatornában."""
    if not me or not hasattr(channel, "permissions_for"):
        return True, ""
    perms = channel.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("👁️ **Csatorna Megtekintése** (View Channel / Read Messages)")
    if not perms.send_messages:
        missing.append("💬 **Üzenetek Küldése** (Send Messages)")
    if not perms.embed_links:
        missing.append("🔗 **Beágyazott Hivatkozások** (Embed Links)")
    if not perms.read_message_history:
        missing.append("📜 **Üzenetelőzmények Olvasása** (Read Message History)")
    
    if missing:
        return False, "\n".join([f"• {m}" for m in missing])
    return True, ""


bot = QuestBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log(f"Slash parancs hiba [{interaction.command.name if interaction.command else 'Unknown'}]: {error}", "error")
    
    if isinstance(error, app_commands.TransformerError):
        msg = f"{config.EMOJI_FAIL} **Érvénytelen csatorna paraméter:** `{error.value}` nem érvényes csatorna ehhez a parancshoz."
    else:
        msg = f"{config.EMOJI_FAIL} **Hiba történt a parancs végrehajtása közben:** `{error}`"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        log(f"Nem sikerült hibaüzenetet küldeni a felhasználónak: {e}", "warn")


import typing


# Slash Command: Set up channel by selecting channel or current channel
@bot.tree.command(name="channel", description="[Admin] Működési csatorna beállítása (üresen hagyva a jelenlegi csatorna)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(target_channel="(Opcionális) Válassz szöveges csatornát. Üresen hagyva az aktuális csatorna lesz beállítva.")
async def set_channel(
    interaction: discord.Interaction,
    target_channel: typing.Optional[discord.TextChannel] = None
):
    # Verify Admin permission
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    channel = target_channel or interaction.channel

    if not channel or not hasattr(channel, "send"):
        await interaction.followup.send(f"{config.EMOJI_FAIL} **Nem sikerült érvényes szöveges csatornát azonosítani!** Kérjük, szöveges csatornában add ki a parancsot.", ephemeral=True)
        return

    # Check bot permissions in target channel
    me = interaction.guild.me if interaction.guild else None
    has_perms, missing_str = check_bot_channel_permissions(channel, me)
    if not has_perms:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} **A bot nem rendelkezik elegendő jogosultsággal a(z) {getattr(channel, 'mention', channel.name)} csatornában!**\n\n"
            f"Kérjük nyisd meg a **Csatornabeállítások ➔ Jogosultságok (Permissions)** menüt és add meg a következő jogokat:\n"
            f"{missing_str}",
            ephemeral=True
        )
        return

    # Save to dynamic config for this specific server guild
    guild_id = interaction.guild_id if interaction.guild else None
    save_channel_config(channel.id, guild_id=guild_id)

    # Recreate the control panel for this channel
    await bot.update_control_panel(target_channel=channel)

    channel_mention = getattr(channel, "mention", f"<#{channel.id}>")
    await interaction.followup.send(f"{config.EMOJI_SUCCESS} **A működési csatorna sikeresen beállítva itt:** {channel_mention}", ephemeral=True)


# Slash Command: Quick Setup in Current Channel
@bot.tree.command(name="setup_here", description="[Admin] Vezérlőpanel kihelyezése közvetlenül az aktuális csatornába")
@app_commands.default_permissions(administrator=True)
async def setup_here(interaction: discord.Interaction):
    # Verify Admin permission
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    target_channel = interaction.channel
    if not target_channel or not hasattr(target_channel, "send"):
        await interaction.followup.send(f"{config.EMOJI_FAIL} **A jelenlegi csatorna nem támogatja az üzenetküldést!**", ephemeral=True)
        return

    # Check bot permissions in target channel
    me = interaction.guild.me if interaction.guild else None
    has_perms, missing_str = check_bot_channel_permissions(target_channel, me)
    if not has_perms:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} **A bot nem rendelkezik elegendő jogosultsággal a(z) {getattr(target_channel, 'mention', target_channel.name)} csatornában!**\n\n"
            f"Kérjük nyisd meg a **Csatornabeállítások ➔ Jogosultságok (Permissions)** menüt és add meg a következő jogokat:\n"
            f"{missing_str}",
            ephemeral=True
        )
        return

    guild_id = interaction.guild_id if interaction.guild else None
    save_channel_config(target_channel.id, guild_id=guild_id)

    await bot.update_control_panel(target_channel=target_channel)

    channel_mention = getattr(target_channel, "mention", f"<#{target_channel.id}>")
    await interaction.followup.send(f"{config.EMOJI_SUCCESS} **A Vezérlőpanel sikeresen kihelyezve ebbe a csatornába:** {channel_mention}", ephemeral=True)


def get_bot_invite_url(bot):
    permissions = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        use_external_emojis=True,
        manage_messages=True,
        manage_channels=True
    )
    return discord.utils.oauth_url(
        bot.user.id,
        permissions=permissions,
        scopes=("bot", "applications.commands")
    )


# Slash Command: Get Bot Invite Link
@bot.tree.command(name="invite", description="A Quest Bot szervermeghívó linkjének lekérése")
async def invite_bot(interaction: discord.Interaction):
    from txa_layout import invite_view
    await interaction.response.send_message(view=invite_view(interaction.user.id), ephemeral=True)


def is_admin_or_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id in config.ADMIN_IDS:
        return True
    if interaction.guild and getattr(interaction.user, 'guild_permissions', None) and interaction.user.guild_permissions.administrator:
        return True
    return False


# Slash Command: Start/Join guide (slash command)
@bot.tree.command(name="start", description="Automatikus quest teljesítés indítása vagy útmutató a működési csatornához")
async def start_command(interaction: discord.Interaction):
    server_channel_id = resolve_guild_channel_id(interaction.guild)

    if not server_channel_id:
        await interaction.response.send_message(
            f"{config.EMOJI_FAIL} **Ezen a szerveren még nincs beállítva működési csatorna!** "
            "Kérd meg a szerver adminisztrátorát a `/channel` vagy `/setup_here` parancs futtatására.",
            ephemeral=True
        )
        return

    if is_panel_channel(interaction):
        await handle_qr_start(interaction)
    else:
        await interaction.response.send_message(
            f"📢 **A küldetések indításához kérjük lépj a szerver kijelölt csatornájába:** <#{server_channel_id}>\n"
            f"Ott kattints az **Indítás** 🚀 gombra vagy használd a `/start` parancsot a QR-kód beolvasásához.",
            ephemeral=True
        )


# Command: Global status checker (slash command)
@bot.tree.command(name="status", description="[Admin] Futó munkamenetek és rendszerállapot megtekintése")
async def show_status(interaction: discord.Interaction):
    if not is_admin_or_owner(interaction):
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    from txa_layout import StatusLayoutView
    await interaction.response.send_message(
        view=StatusLayoutView(interaction.user.id, bot, bot_start_time), ephemeral=True
    )


# Slash Command: View detailed bot configuration (slash command for Admin)
@bot.tree.command(name="config", description="[Admin] Rendszerbeállítások és konfiguráció megtekintése")
@app_commands.default_permissions(administrator=True)
async def view_config(interaction: discord.Interaction):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    from txa_layout import ConfigLayoutView
    await interaction.response.send_message(view=ConfigLayoutView(interaction.user.id), ephemeral=True)


# Prefix parancs: !admin_bypass [true/false]
@bot.command(name="admin_bypass")
async def cmd_admin_bypass(ctx: commands.Context, enabled: str = None):
    if ctx.author.id not in config.ADMIN_IDS:
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if enabled is None:
        curr = getattr(config, "ADMIN_BYPASS_LIMIT", True)
        status_txt = "👑 **BE (Admin korlátlan kvóta)**" if curr else "🛡️ **KI (Admin normál felhasználóként korlátozva)**"
        await ctx.send(f"⚙️ Jelenlegi `ADMIN_BYPASS_LIMIT` állapot: {status_txt}", delete_after=15)
        return

    is_on = enabled.strip().lower() in ("true", "1", "yes", "on", "be", "igen")
    config.update_env_variable("ADMIN_BYPASS_LIMIT", str(is_on))
    status_txt = "👑 **BE (Admin korlátlan kvóta)**" if is_on else "🛡️ **KI (Admin normál felhasználóként korlátozva)**"
    embed = discord.Embed(
        title="⚙️ Admin Bypass Beállítás Frissítve",
        color=discord.Color.green() if is_on else discord.Color.orange(),
        description=(
            f"{config.EMOJI_SUCCESS} Az `ADMIN_BYPASS_LIMIT` sikeresen frissítve: {status_txt}\n\n"
            f"• **Memória:** Azonnal érvénybe lépett\n"
            f"• **Fájl:** Mentve a `.env` fájlba"
        ),
        timestamp=discord.utils.utcnow()
    )
    await ctx.send(embed=embed, delete_after=15)


# Prefix parancs: !set_env <kulcs> <érték>
@bot.command(name="set_env")
async def cmd_set_env(ctx: commands.Context, key: str = None, value: str = None):
    if ctx.author.id not in config.ADMIN_IDS:
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if not key or not value:
        await ctx.send(f"{config.EMOJI_FAIL} Használat: `!set_env <KULCS> <ÉRTÉK>` (Pl: `!set_env ADMIN_BYPASS_LIMIT False`)", delete_after=10)
        return

    clean_key = key.strip().upper()
    clean_val = value.strip()

    if clean_key in ("ADMIN_BYPASS_LIMIT", "AUTO_ACCEPT", "LOG_PROGRESS", "DEBUG"):
        if clean_val.lower() not in ("true", "false", "1", "0", "yes", "no"):
            await ctx.send(f"{config.EMOJI_FAIL} A(z) `{clean_key}` változó csak `True` vagy `False` értéket fogad el.", delete_after=10)
            return
    elif clean_key in ("POLL_INTERVAL", "HEARTBEAT_INTERVAL"):
        if not clean_val.isdigit() or int(clean_val) < 1:
            await ctx.send(f"{config.EMOJI_FAIL} A(z) `{clean_key}` változónak pozitív egész számnak kell lennie (másodperc).", delete_after=10)
            return

    config.update_env_variable(clean_key, clean_val)
    embed = discord.Embed(
        title="⚙️ Környezeti Változó Frissítve",
        color=discord.Color.green(),
        description=(
            f"{config.EMOJI_SUCCESS} A(z) **`{clean_key}`** értéke sikeresen beállítva: **`{clean_val}`**\n\n"
            f"• **Memória:** Azonnal érvénybe lépett\n"
            f"• **Fájl:** Véglegesen mentve a `.env` fájlba"
        ),
        timestamp=discord.utils.utcnow()
    )
    await ctx.send(embed=embed, delete_after=15)


# Command: Admin set quickly quest limits/balance (slash command for Admin)
@bot.tree.command(name="set_limit", description="[Admin] Felhasználói quest egyenleg (kvóta) közvetlen beállítása")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user_discord_id="Válaszd ki vagy add meg a Discord felhasználó ID-ját", balance="Beállítani kívánt küldetéslehetőségek száma (pl: 5)")
async def set_limit_command(interaction: discord.Interaction, user_discord_id: str, balance: int):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    if not user_discord_id.isdigit():
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Érvénytelen felhasználó azonosító.", ephemeral=True)
        return

    if balance < 0:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} A lehetőségek számának legalább 0-nak kell lennie.", ephemeral=True)
        return

    from limits_manager import load_limits, save_limits
    uid = user_discord_id
    limits = load_limits()
    if uid not in limits:
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0,
            "total_completed": 0,
            "active_transaction": None
        }
    
    old_balance = limits[uid].get("purchased_balance", 0)
    limits[uid]["purchased_balance"] = balance
    save_limits(limits)

    embed = discord.Embed(
        title="🎯 Egyenleg Sikeresen Beállítva",
        color=discord.Color.from_rgb(88, 101, 242),
        description=f"A kvóta sikeresen **beállítva** a következő felhasználónak: <@{uid}>."
    )
    embed.add_field(name="👤 Felhasználó", value=f"<@{uid}> (ID: `{uid}`)", inline=False)
    embed.add_field(name="📉 Előtte", value=f"`{old_balance:02d} lehetőség`", inline=True)
    embed.add_field(name="📈 Utána", value=f"`{balance:02d} lehetőség`", inline=True)
    embed.add_field(name="🔁 Változás", value=f"`{balance - old_balance:+d}`", inline=True)
    embed.set_footer(text="/set_limit abszolút érték • /add_balance hozzáadás")

    from txa_layout import embed_to_view
    await interaction.response.send_message(view=embed_to_view(embed, interaction.user.id), ephemeral=True)


@set_limit_command.autocomplete("user_discord_id")
async def set_limit_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    if interaction.user.id not in config.ADMIN_IDS:
        return []

    from limits_manager import load_limits
    limits = load_limits()
    choices = []
    current_lower = current.strip().lower()
    
    seen = set()
    for uid, info in config.ACTIVE_USERS.items():
        seen.add(str(uid))
        uname = info.get("username", "Futásban")
        label = f"[Futásban] {uname} ({uid})"
        if not current_lower or current_lower in label.lower() or current_lower in str(uid):
            choices.append(app_commands.Choice(name=label[:100], value=str(uid)))

    for uid, udata in limits.items():
        if uid in seen or not str(uid).isdigit():
            continue
        balance = udata.get("purchased_balance", 0) if isinstance(udata, dict) else 0
        label = f"ID: {uid} (Egyenleg: {balance} lehetőség)"
        if not current_lower or current_lower in label.lower() or current_lower in str(uid):
            choices.append(app_commands.Choice(name=label[:100], value=str(uid)))
            if len(choices) >= 25:
                break

    return choices[:25]


# Command: Admin clear pycache and compile files (slash command for Admin)
@bot.tree.command(name="clear_cache", description="[Admin] __pycache__ könyvtárak törlése és tárhely felszabadítása")
@app_commands.default_permissions(administrator=True)
async def clear_cache_command(interaction: discord.Interaction):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    import shutil
    deleted_files = 0
    deleted_dirs = 0
    total_size = 0
    root_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        for root, dirs, files in os.walk(root_dir, topdown=False):
            for file in files:
                if file.endswith('.pyc') or file.endswith('.pyo'):
                    fp = os.path.join(root, file)
                    try:
                        total_size += os.path.getsize(fp)
                        os.remove(fp)
                        deleted_files += 1
                    except Exception:
                        pass
            for d in dirs:
                if d == '__pycache__':
                    dp = os.path.join(root, d)
                    try:
                        for r, ds, fs in os.walk(dp):
                            for f in fs:
                                total_size += os.path.getsize(os.path.join(r, f))
                        shutil.rmtree(dp)
                        deleted_dirs += 1
                    except Exception:
                        pass

        size_kb = total_size / 1024
        size_str = f"{size_kb:.2f} KB" if size_kb < 1024 else f"{size_kb / 1024:.2f} MB"

        embed = discord.Embed(
            title="🧹 Gyorsítótár Sikeresen Kiürítve",
            color=discord.Color.green(),
            description="Az átmeneti cache és fordítási fájlok törölve lettek."
        )
        embed.add_field(name="📁 Törölt könyvtárak", value=f"`{deleted_dirs} __pycache__ mappa`", inline=True)
        embed.add_field(name="📄 Törölt fájlok", value=f"`{deleted_files} .pyc/.pyo fájl`", inline=True)
        embed.add_field(name="💾 Felszabadított tárhely", value=f"`{size_str}`", inline=False)
        embed.set_footer(text="Quest Bot Karbantartó Rendszer")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Hiba történt a gyorsítótár ürítésekor: `{e}`", ephemeral=True)


async def get_stop_user_choices(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    if not is_admin_or_owner(interaction):
        return []

    if not config.ACTIVE_USERS:
        return [app_commands.Choice(name="⚠️ Jelenleg nincs futó munkamenet a rendszerben", value="none")]

    choices = []
    current_lower = current.strip().lower()
    for uid, info in list(config.ACTIVE_USERS.items()):
        uname = info.get("username", "Unknown")
        d_user = info.get("discord_user")
        d_name = d_user.name if d_user else ""
        completer = info.get("completer")

        curr_info = getattr(completer, "current_quest_info", None) if completer else None
        if curr_info:
            q_name = curr_info.get("name", "Quest")[:14]
            done = curr_info.get("done", 0)
            needed = curr_info.get("needed", 1)
            pct = int((done / needed * 100)) if needed else 0
            detail = f"[{q_name} {pct}%]"
        else:
            detail = "[Beolvasás...]"

        display_label = f"🛑 {uname} {detail} • ID: {uid}"
        if d_name and d_name != uname:
            display_label = f"🛑 {uname} ({d_name}) {detail} • ID: {uid}"

        if not current_lower or current_lower in display_label.lower() or current_lower in str(uid):
            choices.append(app_commands.Choice(name=display_label[:100], value=str(uid)))
            if len(choices) >= 25:
                break
    return choices


# Command: Force stop a user (slash command)
@bot.tree.command(name="stop_user", description="[Admin] Egy felhasználó quest munkamenetének kényszerített leállítása")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user_discord_id="Válaszd ki a leállítani kívánt aktív felhasználót")
async def stop_user(interaction: discord.Interaction, user_discord_id: str):
    if not is_admin_or_owner(interaction):
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    if user_discord_id == "none" or not user_discord_id.isdigit():
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Kérjük válassz egy érvényes futó munkamenetet a listából.", ephemeral=True)
        return

    uid = int(user_discord_id)
    if uid not in config.ACTIVE_USERS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a felhasználó jelenleg nem aktív, vagy a folyamat már véget ért.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    info = config.ACTIVE_USERS.pop(uid, None)
    if info:
        try:
            await info['completer'].stop()
            if info['task']:
                info['task'].cancel()

            # Send a DM to that user informing them they were stopped
            user = bot.get_user(uid)
            if not user:
                user = await bot.fetch_user(uid)
            if user:
                await user.send("🛑 **A küldetés munkamenetedet egy adminisztrátor leállította.**")
        except Exception as e:
            log(f"Hiba a felhasználó leállításakor: {e}", "warn")

        await bot.update_control_panel()
        await interaction.followup.send(f"{config.EMOJI_SUCCESS} **A folyamat sikeresen leállítva a következő felhasználónál:** **{info['username']}** (ID: `{uid}`)", ephemeral=True)
    else:
        await interaction.followup.send(f"{config.EMOJI_FAIL} A munkamenet már lezárult.", ephemeral=True)


@stop_user.autocomplete("user_discord_id")
async def stop_user_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    return await get_stop_user_choices(interaction, current)


# Command: Short /stop alias (handles both Admin stopping users & User stopping self)
@bot.tree.command(name="stop", description="Quest munkamenet leállítása (Admin másokét, Felhasználó a sajátját)")
@app_commands.describe(user_discord_id="[Admin] Válaszd ki a leállítandó felhasználót (üresen hagyva a sajátodat állítja le)")
async def stop_command(interaction: discord.Interaction, user_discord_id: str = None):
    if user_discord_id and user_discord_id != "none":
        await stop_user(interaction, user_discord_id)
    else:
        await stop_my_session(interaction)


@stop_command.autocomplete("user_discord_id")
async def stop_command_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    return await get_stop_user_choices(interaction, current)


class QuestClaimView(discord.ui.View):
    def __init__(self, completer, quest_choices, user_id):
        super().__init__(timeout=180)  # Timeout after 3 minutes
        self.completer = completer
        self.user_id = user_id

        # Dynamically add buttons for each unclaimed quest
        for quest_id, quest_name in quest_choices.items():
            button = discord.ui.Button(
                label=f"Átvétel: {quest_name[:20]}",
                style=discord.ButtonStyle.primary,
                emoji="🎁",
                custom_id=f"claim_{quest_id}"
            )
            button.callback = self.make_callback(quest_id, quest_name)
            self.add_item(button)

    def make_callback(self, quest_id, quest_name):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(f"{config.EMOJI_FAIL} Ez a gomb nem neked szól.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            target_btn = None
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.custom_id == f"claim_{quest_id}":
                    target_btn = child
                    break

            res = await self.completer.claim_reward(quest_id)
            await handle_claim_reward_response(res, quest_name, interaction, target_btn, self)
        return callback


# Command: Help command (slash command)
@bot.tree.command(name="help", description="Részletes használati útmutató és működés (QR-kódos belépés)")
async def help_command(interaction: discord.Interaction):
    from txa_layout import HelpLayoutView
    await interaction.response.send_message(view=HelpLayoutView(interaction.user.id), ephemeral=True)


# Command: View active session details (slash command)
async def send_session_details(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed, view = await build_session_details_response(interaction.user.id, interaction.user, bot)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="details", description="Valós idejű küldetés-haladás vagy felhasználói egyenleg megtekintése")
async def details_command(interaction: discord.Interaction):
    await send_session_details(interaction)


# Command: View active user quests (slash command alias for details)
@bot.tree.command(name="my_quests", description="Küldetések állapotának megtekintése és ajándékkódok beváltása")
async def my_quests(interaction: discord.Interaction):
    await send_session_details(interaction)


# Command: View detailed active user Discord info (slash command)
@bot.tree.command(name="user_info", description="Az éppen futó Discord fiók részletes profilinformációi")
async def user_info_command(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id not in config.ACTIVE_USERS:
        channel_mention = panel_mention_for_user(interaction.user.id)
        await interaction.response.send_message(
            f"{config.EMOJI_FAIL} **Jelenleg nincs aktív futó munkameneted!**\n"
            f"Kérjük látogass el a(z) {channel_mention} csatornába az indításhoz.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    info = config.ACTIVE_USERS[user_id]
    api = info['completer'].api

    r = await api.get("/users/@me")
    if r.status != 200:
        await interaction.followup.send(
            f"{config.EMOJI_FAIL} Nem sikerült lekérni a fiókinformációkat a Discord API-tól.",
            ephemeral=True
        )
        return

    user_data = await r.json()
    
    # Extract details
    username = user_data.get("username", "?")
    global_name = user_data.get("global_name") or "Nincs megadva"
    uid = user_data.get("id", "?")
    email = user_data.get("email") or "Nincs megadva"
    phone = user_data.get("phone") or "Nincs megadva"
    mfa = "Bekapcsolva 🔐" if user_data.get("mfa_enabled") else "Kikapcsolva 🔓"
    locale = user_data.get("locale", "en-US")
    
    premium_type = user_data.get("premium_type", 0)
    nitro_map = {
        0: "Nincs ❌",
        1: "Nitro Classic 🎫",
        2: "Nitro Boost 🚀",
        3: "Nitro Basic 💎"
    }
    nitro_str = nitro_map.get(premium_type, "Ismeretlen")

    # Creation date from snowflake ID
    try:
        snowflake_time = int(uid) >> 22
        creation_timestamp = int((snowflake_time + 1420070400000) / 1000)
        creation_str = f"<t:{creation_timestamp}:R> (<t:{creation_timestamp}:F>)"
    except Exception:
        creation_str = "Ismeretlen"

    # Profile Avatar URL
    avatar_hash = user_data.get("avatar")
    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.png?size=256"
    else:
        try:
            disc = int(user_data.get("discriminator", "0"))
            if disc == 0:
                avatar_index = (int(uid) >> 22) % 6
            else:
                avatar_index = disc % 5
        except Exception:
            avatar_index = 0
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{avatar_index}.png"

    from txa_layout import SimpleLayoutView
    display = global_name if global_name != "Nincs megadva" else username
    body = (
        f"```yaml\nUsername : {username}\nUser ID  : {uid}\nLocale   : {locale}\n```\n"
        f"💎 **Nitro:** {nitro_str}  •  🔒 **MFA:** {mfa}\n"
        f"📧 **Email:** ||`{email}`||\n"
        f"📞 **Telefonszám:** ||`{phone}`||\n"
        f"📅 **Fiók létrehozva:** {creation_str}"
    )
    view = SimpleLayoutView(
        interaction.user.id, f"👤 {display}", body, thumb=avatar_url,
        footer="Az email és telefonszám rejtett (spoiler) • Csak te látod ezt az üzenetet",
    )
    await interaction.followup.send(view=view, ephemeral=True)


# Command: View system stats (slash command)
@bot.tree.command(name="system_stats", description="Szerver telemetria, hardver terheltség és állapot valós időben")
async def system_stats(interaction: discord.Interaction):
    from txa_layout import SystemLayoutView
    view = SystemLayoutView(interaction.user.id, bot, bot_start_time)
    await interaction.response.send_message(view=view, ephemeral=True)
    view.start(interaction)


# Command: Stop active session for calling user (slash command)
@bot.tree.command(name="stop_my_session", description="Saját aktív quest munkameneted leállítása")
async def stop_my_session(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id not in config.ACTIVE_USERS:
        channel_mention = panel_mention_for_user(interaction.user.id)
        await interaction.response.send_message(
            f"{config.EMOJI_FAIL} **Jelenleg nincs aktív futó küldetés munkameneted!**\n"
            f"Kérjük látogass el a(z) {channel_mention} csatornába az indításhoz.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    
    info = config.ACTIVE_USERS.pop(user_id)
    try:
        await info['completer'].stop()
        if info['task']:
            info['task'].cancel()
    except Exception as e:
        log(f"Hiba a munkamenet leállításakor a felhasználó által: {e}", "warn")

    await bot.update_control_panel()
    await interaction.followup.send(
        "🛑 **Az automatikus küldetésfolyamatod sikeresen leállítva.**\n"
        "Minden erőforrás sikeresen felszabadítva.",
        ephemeral=True
    )


# Command: Admin test QR payment interface (slash command)
@bot.tree.command(name="qr_test", description="[Admin] Teszt fizetési felület megjelenítése")
@app_commands.default_permissions(administrator=True)
async def qr_test(interaction: discord.Interaction):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    
    # Clean previous transaction to allow repeated testing
    from limits_manager import cancel_transaction
    cancel_transaction(interaction.user.id)
    
    from payment_views import get_limit_embed_and_view, register_invoice
    _, view = get_limit_embed_and_view(interaction.user.id)
    await interaction.followup.send(view=view, ephemeral=True)
    register_invoice(interaction, view)


# Command: Admin set price per quest (slash command)
@bot.tree.command(name="set_price", description="[Admin] Egységár beállítása küldetésenként")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(price="Új egységár küldetésenként (pl: 500)")
async def set_price(interaction: discord.Interaction, price: int):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    if price <= 0:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Az egységárnak nagyobbnak kell lennie 0-nál.", ephemeral=True)
        return

    from limits_manager import set_price_per_quest
    set_price_per_quest(price)
    
    from txa_layout import text_card
    from limits_manager import PACKAGES, get_package_info
    table = "\n".join(
        f"• **{q} lehetőség** — {get_package_info(q)['final']:,} Ft" + (f" `-{get_package_info(q)['pct']}%`" if get_package_info(q)['pct'] else "")
        for q in PACKAGES
    )
    await interaction.response.send_message(view=text_card(
        interaction.user.id, f"{config.EMOJI_SUCCESS} Egységár Sikeresen Frissítve",
        f"Új egységár: **{price:,} Ft** / lehetőség.\n\n**🎁 Új csomagajánlatok (véletlenszerű kedvezmények):**\n{table}",
        footer="Az árváltoztatás újrasorsolja a csomagkedvezményeket"
    ), ephemeral=True)


# Command: Admin set transaction timeout (slash command)
@bot.tree.command(name="set_timeout", description="[Admin] Fizetési határidő (időkorlát) beállítása percekben")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(minutes="Lejárati idő percekben (pl: 5)")
async def set_timeout(interaction: discord.Interaction, minutes: int):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak konfigurált Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return

    if minutes <= 0:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} A lejárati időnek legalább 1 percnek kell lennie.", ephemeral=True)
        return

    from limits_manager import set_transaction_timeout
    seconds = minutes * 60
    set_transaction_timeout(seconds)
    
    from txa_layout import text_card
    await interaction.response.send_message(view=text_card(
        interaction.user.id, f"{config.EMOJI_SUCCESS} Számla Érvényességi Idő Frissítve",
        f"A számla érvényessége ezentúl: **{minutes} perc** ({seconds} másodperc).",
        footer="Az új számlákra érvényes"
    ), ephemeral=True)


# Command: Buy quest turns (slash command)
@bot.tree.command(name="buy", description="További quest lehetőségek vásárlása")
async def buy_quests(interaction: discord.Interaction):
    from payment_views import get_limit_embed_and_view, register_invoice
    _, view = get_limit_embed_and_view(interaction.user.id)
    await interaction.response.send_message(view=view, ephemeral=True)
    register_invoice(interaction, view)


# Command: Payment history
@bot.tree.command(name="history", description="Korábbi befizetések és vásárlások előzményei")
async def history_command(interaction: discord.Interaction):
    from txa_layout import history_view
    await interaction.response.send_message(view=history_view(interaction.user.id), ephemeral=True)


# Command: Leaderboard
@bot.tree.command(name="leaderboard", description="Ranglista: a legtöbb küldetést teljesítő felhasználók")
async def leaderboard_command(interaction: discord.Interaction):
    from txa_layout import leaderboard_view
    await interaction.response.send_message(view=leaderboard_view(interaction.user.id), ephemeral=True)


# Command: Ping
@bot.tree.command(name="ping", description="A bot válaszidejének és késleltetésének (ping) ellenőrzése")
async def ping_command(interaction: discord.Interaction):
    from txa_layout import ping_view
    await interaction.response.send_message(view=ping_view(interaction.user.id, bot), ephemeral=True)


# Parancs: Admin egyenleg hozzáadás (add_balance)
@bot.tree.command(name="add_balance", description="[Admin] Quest lehetőségek hozzáadása vagy levonása egy felhasználótól")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user_discord_id="Válaszd ki vagy add meg a felhasználó ID-ját", amount="Hozzáadandó lehetőségek száma (negatív szám levonáshoz)")
async def add_balance_command(interaction: discord.Interaction, user_discord_id: str, amount: int):
    if interaction.user.id not in config.ADMIN_IDS:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} **Nincs jogosultságod a parancs végrehajtásához!** Csak Adminisztrátorok számára engedélyezett.", ephemeral=True)
        return
    if not user_discord_id.isdigit():
        await interaction.response.send_message(f"{config.EMOJI_FAIL} Érvénytelen felhasználó azonosító.", ephemeral=True)
        return
    if amount == 0:
        await interaction.response.send_message(f"{config.EMOJI_FAIL} A megadott mennyiség nem lehet 0.", ephemeral=True)
        return

    from limits_manager import load_limits, save_limits
    uid = user_discord_id
    limits = load_limits()
    if not isinstance(limits.get(uid), dict):
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0,
            "total_completed": 0,
            "active_transaction": None
        }
    old_balance = limits[uid].get("purchased_balance", 0)
    new_balance = max(0, old_balance + amount)
    limits[uid]["purchased_balance"] = new_balance
    save_limits(limits)

    is_self = str(interaction.user.id) == uid
    embed = discord.Embed(
        title="➕ Egyenleg Sikeresen Jóváírva" if amount > 0 else "➖ Egyenleg Sikeresen Levonva",
        color=discord.Color.green() if amount > 0 else discord.Color.orange(),
        description=f"Sikeresen {'jóváírva' if amount > 0 else 'levonva'} **{abs(amount)} lehetőség** a következő fiókhoz: {'**saját fiókod**' if is_self else f'<@{uid}>'}."
    )
    embed.add_field(name="👤 Felhasználó", value=f"<@{uid}> (ID: `{uid}`)", inline=False)
    embed.add_field(name="📉 Előtte", value=f"`{old_balance:02d} lehetőség`", inline=True)
    embed.add_field(name="📈 Utána", value=f"`{new_balance:02d} lehetőség`", inline=True)
    embed.add_field(name="🔁 Változás", value=f"`{new_balance - old_balance:+d}`", inline=True)
    embed.set_footer(text="/add_balance hozzáadás • /set_limit abszolút érték beállítása")
    from txa_layout import embed_to_view
    await interaction.response.send_message(view=embed_to_view(embed, interaction.user.id), ephemeral=True)


@add_balance_command.autocomplete("user_discord_id")
async def add_balance_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await set_limit_autocomplete(interaction, current)


# Command: Cascading menu hub
@bot.tree.command(name="menu", description="Központi vezérlőpult: 3 szintű interaktív menürendszer")
async def menu_command(interaction: discord.Interaction):
    from txa_layout import MenuLayoutView
    await interaction.response.send_message(view=MenuLayoutView(interaction.user.id), ephemeral=True)


def run_bot():
    if os.environ.get("TXA_LAUNCHED") != "1":
        log("=" * 65, "error")
        log("❌ NEM INDÍTHATOD EL KÖZVETLENÜL A BOT.PY FÁJLT!", "error")
        log("👉 Kérjük, indítsd a botot a rendes indítófájllal: python txa.py", "warn")
        log("=" * 65, "error")
        sys.exit(1)

    missing = []
    
    token = config.BOT_TOKEN.strip()
    if not token or "YOUR_DISCORD_BOT_TOKEN" in token or token == "":
        missing.append("BOT_TOKEN (Discord Bot Token a Discord Developer Portalról)")
        
    if not config.ADMIN_IDS:
        missing.append("ADMIN_IDS (A Discord fiókod ID-ja az Admin jogosultsághoz)")

    if missing:
        log("=" * 65, "error")
        log("❌ HIÁNYZÓ VAGY ÉRVÉNYTELEN KONFIGURÁCIÓ A .env FÁJLBAN", "error")
        log("Kérjük pótold a következő adatokat az indítás előtt:", "error")
        for item in missing:
            log(f"   👉 Hiányzik: {item}", "error")
        log("💡 Útmutató: Nyisd meg a `.env` fájlt, töltsd ki az adataiddal és mentsd el.", "info")
        log("=" * 65, "error")
        return
    
    banner = f"""{Colors.CYAN}{Colors.BOLD}
============================================================
       🤖 DISCORD QUEST AUTO-COMPLETER BOT v4.0 🤖
                🚀 Built by TXA 🚀
       A forráskód aszinkron működésre optimalizálva
============================================================{Colors.RESET}"""
    print(banner)
    log("Discord Bot indítása folyamatban...", "info")
    
    try:
        bot.run(config.BOT_TOKEN)
    except KeyboardInterrupt:
        log("Ctrl+C jelzés fogadva. A rendszer leáll...", "warn")

if __name__ == "__main__":
    log("=" * 65, "error")
    log("❌ NEM INDÍTHATOD EL KÖZVETLENÜL A BOT.PY FÁJLT!", "error")
    log("👉 Kérjük, indítsd a botot a rendes indítófájllal: python txa.py", "warn")
    log("=" * 65, "error")
    sys.exit(1)
