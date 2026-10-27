# 🤖 TXA Discord Quest Bot v4.0

> **Automatikus Discord Quest Teljesítő Bot** — Szerezz Gift Kódokat, Discord Nitrót, Avatar Díszeket és Profil Dekorációkat anélkül, hogy órákat töltenél kézi játékkal vagy videók nézésével!

---

## ✨ Főbb Funkciók

- 📱 **100% Mobil QR-Kódos Belépés (Discord Remote Auth v2)** — **Zéró manuális token másolgatás!** A bejelentkezés a hivatalos Discord mobilalkalmazás beépített kamerás beolvasójával történik RSA-2048 végponti titkosítással. Nem szükséges és nem is kell tokent kimásolnod a böngészőből!
- 🚀 **Teljesen Automata Működés** — A bot magától felderíti a fiókodon elérhető küldetéseket, automatikusan elfogadja őket, és a háttérben szimulálja a feladatokat (videó, játék, stream, aktivitás).
- 📊 **Élő Értesítések és Folyamatjelző** — A privát üzeneteidben (DM) másodpercre pontos, animált folyamatjelzőn követheted nyomon a küldetések haladását és a hátralévő időt.
- 🎁 **Automatikus Jutalom- és Kódbeváltás** — Amint elkészül egy küldetés, a bot azonnal beváltja vagy elküldi a promóciós ajándékkódot.
- 💎 **Rugalmas Kvótarendszer** — Felhasználónkénti és szerverenkénti limitkezelés, ranglista és egyenleglekérdezés.
- 👑 **Korszerű Rendszerfelügyelet** — Valós idejű telemetria, memóriahasználat, CPU-terheltség és interaktív vezérlőpult.

---

## 🚀 Használati Útmutató Felhasználóknak

### 1. Lépés — Privát üzenetek (DM) engedélyezése
A bot a privát üzeneteiden keresztül tájékoztat a feladatok állapotáról. Győződj meg róla, hogy a szerveren engedélyezve vannak a közvetlen üzenetek:
1. Kattints a szerver nevére a bal felső sarokban.
2. Válaszd az **Adatvédelmi beállítások** (Privacy Settings) menüpontot.
3. Kapcsold be a **"Közvetlen üzenetek engedélyezése"** (Direct Messages) opciót.

### 2. Lépés — Belépés és Indítás QR-Kóddal
1. Lépj be a szerver kijelölt bot csatornájába (pl. `#bot-parancsok`).
2. Kattints a zöld **Indítás** 🚀 gombra a vezérlőpanelen, vagy használd a `/start` parancsot.
3. A bot egy privát (csak általad látható) üzenetben megjelenít egy **egyedi QR-kódot**.
4. Vedd a kezedbe a telefonodat és nyisd meg a **Discord appot**.
5. Koppints a profilodra a jobb alsó sarokban ➔ **Beállítások ➔ QR-kód beolvasása**.
6. Olvasd be a kamerával a monitorodon látható QR-kódot, majd a telefonodon koppints az **"Igen, bejelentkezem"** gombra!

### 3. Lépés — Élvezd az Automata Teljesítést
- A megerősítés után a bot azonnal elindítja a küldetéseket.
- A privát üzeneteidben (DM) azonnal megnyílik az élő állapotjelző felület.
- Használd a `/my_quests` parancsot az aktuális küldetéslista megtekintéséhez vagy a `/stop_my_session` parancsot a folyamat bármikori megszakításához.

---

## 🎯 Támogatott Küldetéstípusok

| Küldetés Típusa | Leírás | Teljesítési Mód |
|---|---|---|
| 🎬 **Watch Video** | Promóciós videók és trailerek megtekintése | Automatikus háttér-lejátszás |
| 🎮 **Play on Desktop** | Kijelölt PC-s játékok futtatása | Automatikus játékmenet szimuláció |
| 📡 **Stream on Desktop** | Játék közvetítése barátoknak Discord hangcsatornában | Automata stream heartbeat emuláció |
| 🕹️ **Play Activity** | Beépített Discord Party játékok és aktivitások | Automatikus aktivitási munkamenet |

---

## 👑 Rendszergazdai & Telepítési Útmutató

### Rendszerkövetelmények
- **Python:** 3.10 vagy újabb
- **Függőségek:** `discord.py`, `aiohttp`, `cryptography`, `qrcode`, `Pillow`, `psutil`, `python-dotenv`

### Telepítés & Futtatás

```bash
# Függőségek telepítése
pip install -r requirements.txt

# Indítás tiszta háttérfolyamatként
python -u txa.py
```

### Konfiguráció (`.env`)

```env
# Discord Bot Token és Adminisztrátorok
BOT_TOKEN=a_te_discord_bot_tokened
ADMIN_IDS=1095731086513930260
CHANNEL_ID=1158023868619956235

# Teljesítmény és időzítések
POLL_INTERVAL=30
HEARTBEAT_INTERVAL=20
AUTO_ACCEPT=True
LOG_PROGRESS=True
DEBUG=True
```

### Elérhető Főbb Slash Parancsok

- `/start` — Küldetésindítás és QR-kód kérése
- `/menu` — Interaktív, 3 szintű vezérlőközpont
- `/my_quests` — Aktuális küldetések és megszerzett kódok listája
- `/details` — Részletes élő állapot és feladatlista
- `/status` — [Admin] Futó felhasználói munkamenetek listája
- `/channel` — [Admin] Működési csatorna konfigurálása
- `/setup_here` — [Admin] Vezérlőpanel kihelyezése az adott csatornába
- `/system_stats` — Valós idejű hardver és szerver telemetria
- `/stop` vagy `/stop_my_session` — Futó munkamenet leállítása
