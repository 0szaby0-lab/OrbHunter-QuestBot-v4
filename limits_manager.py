import os
import json
import time
import random
import string
import aiohttp
from datetime import date
from typing import Optional
from logger import log
import config

LIMITS_FILE = "user_limits.json"

def load_limits() -> dict:
    if os.path.exists(LIMITS_FILE):
        try:
            with open(LIMITS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Hiba a limitek beolvasásakor: {e}", "error")
            return {}
    return {}

def save_limits(limits: dict):
    try:
        # Globális statisztikák szinkronizálása mentés előtt
        limits["total_completed"] = config.TOTAL_COMPLETED
        limits["total_failed"] = config.TOTAL_FAILED
        with open(LIMITS_FILE, "w", encoding="utf-8") as f:
            json.dump(limits, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Hiba a limitek mentésekor: {e}", "error")

def is_user_limited(user_id: int) -> bool:
    """Ellenőrzi, hogy a felhasználó elérte-e a küldetés limitet."""
    if getattr(config, "ADMIN_BYPASS_LIMIT", True) and user_id in config.ADMIN_IDS:
        return False  # Az adminok megkerülik a limitet

    uid = str(user_id)
    limits = load_limits()
    if uid not in limits:
        return False  # Még nem csinált ma küldetést, jár az ingyenes

    user_data = limits[uid]
    purchased_balance = user_data.get("purchased_balance", 0)
    if purchased_balance > 0:
        return False  # Még van megvásárolt lehetősége

    today_str = date.today().isoformat()
    free_date = user_data.get("free_completed_date")
    free_count = user_data.get("free_completed_count", 0)
    
    if free_date == today_str and free_count >= 1:
        return True  # Korlátozva: ma már teljesített 1 ingyenes küldetést

    return False

def consume_limit(user_id: int) -> bool:
    """Levon 1 küldetési lehetőséget (ingyenes vagy vásárolt kvóta)."""
    if getattr(config, "ADMIN_BYPASS_LIMIT", True) and user_id in config.ADMIN_IDS:
        return True

    uid = str(user_id)
    limits = load_limits()
    today_str = date.today().isoformat()

    if uid not in limits:
        limits[uid] = {
            "free_completed_date": today_str,
            "free_completed_count": 1,
            "purchased_balance": 0
        }
        save_limits(limits)
        return True

    user_data = limits[uid]
    purchased_balance = user_data.get("purchased_balance", 0)

    if purchased_balance > 0:
        user_data["purchased_balance"] = purchased_balance - 1
        save_limits(limits)
        log(f"A(z) {user_id} felhasználó elhasznált 1 vásárolt lehetőséget. Hátralévő: {user_data['purchased_balance']}", "ok")
        return True

    free_date = user_data.get("free_completed_date")
    free_count = user_data.get("free_completed_count", 0)

    if free_date == today_str:
        if free_count >= 1:
            log(f"A(z) {user_id} felhasználó kísérletet tett a futtatásra, de elfogyott a mai ingyenes kvótája", "warn")
            return False
        user_data["free_completed_count"] = free_count + 1
    else:
        user_data["free_completed_date"] = today_str
        user_data["free_completed_count"] = 1

    save_limits(limits)
    log(f"A(z) {user_id} felhasználó elhasználta a napi ingyenes lehetőségét", "ok")
    return True

def generate_order_id(limits: dict) -> str:
    """Egyedi rendelési azonosító generálása TQB[A-Z]{2}[0-9]{4} formátumban."""
    while True:
        letters = "".join(random.choices(string.ascii_uppercase, k=2))
        digits = "".join(random.choices(string.digits, k=4))
        order_id = f"TQB{letters}{digits}"
        
        dup = False
        for user_data in limits.values():
            if isinstance(user_data, dict):
                active_tx = user_data.get("active_transaction")
                if active_tx and active_tx.get("order_id") == order_id:
                    dup = True
                    break
        if not dup:
            return order_id

def get_price_per_quest() -> int:
    """Egységár lekérése küldetésenként."""
    limits = load_limits()
    return limits.get("price_per_quest", 500)

def set_price_per_quest(price: int):
    """Új egységár beállítása küldetésenként."""
    limits = load_limits()
    limits["price_per_quest"] = price
    save_limits(limits)
    log(f"Új egységár beállítva: {price} Ft/lehetőség", "ok")
    roll_discounts()

PACKAGES = [1, 2, 5, 10, 20, 50]
DISCOUNT_RANGES = {1: (0, 0), 2: (2, 5), 5: (5, 10), 10: (10, 16), 20: (15, 24), 50: (22, 35)}

def roll_discounts() -> dict:
    discounts, prev = {}, 0
    for qty in PACKAGES:
        lo, hi = DISCOUNT_RANGES[qty]
        pct = max(random.randint(lo, hi), prev)
        discounts[str(qty)] = pct
        prev = pct
    limits = load_limits()
    limits["discounts"] = discounts
    save_limits(limits)
    log(f"Véletlenszerű kedvezmények sorsolva: {discounts}", "ok")
    return discounts

def get_discounts() -> dict:
    limits = load_limits()
    d = limits.get("discounts")
    if not d:
        return roll_discounts()
    return d

def get_package_info(qty: int) -> dict:
    price = get_price_per_quest()
    pct = int(get_discounts().get(str(qty), 0))
    original = price * qty
    final = max(100, int(original * (100 - pct) / 100 / 100.0 + 0.5) * 100)
    return {"qty": qty, "pct": pct, "original": original, "final": final, "saved": original - final}

def create_transaction(user_id: int, quests: int) -> Optional[dict]:
    uid = str(user_id)
    limits = load_limits()
    
    if uid not in limits:
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0
        }
        
    user_data = limits[uid]
    
    if "active_transaction" in user_data and user_data["active_transaction"]:
        return None
        
    order_id = generate_order_id(limits)
    info = get_package_info(quests)
    amount = info["final"]
    
    tx = {
        "order_id": order_id,
        "amount": amount,
        "quests_to_buy": quests,
        "created_at": int(time.time())
    }
    
    user_data["active_transaction"] = tx
    save_limits(limits)
    log(f"Új tranzakció: {user_id}: {order_id} - {amount} Ft {quests} lehetőségre ({info['pct']}% kedvezmény)", "info")
    return tx

def cancel_transaction(user_id: int):
    uid = str(user_id)
    limits = load_limits()
    if uid in limits and "active_transaction" in limits[uid]:
        limits[uid]["active_transaction"] = None
        save_limits(limits)
        log(f"A(z) {user_id} tranzakciója törölve", "info")

def get_transaction_timeout() -> int:
    limits = load_limits()
    return limits.get("transaction_timeout", 300)

def set_transaction_timeout(seconds: int):
    limits = load_limits()
    limits["transaction_timeout"] = seconds
    save_limits(limits)
    log(f"Új tranzakciós időkorlát: {seconds} mp", "ok")

def get_active_transaction(user_id: int) -> Optional[dict]:
    uid = str(user_id)
    limits = load_limits()
    if uid in limits:
        user_data = limits[uid]
        tx = user_data.get("active_transaction")
        if tx:
            created_at = tx.get("created_at", 0)
            timeout = get_transaction_timeout()
            if int(time.time()) - created_at > timeout:
                user_data["active_transaction"] = None
                save_limits(limits)
                log(f"A(z) {user_id} {tx['order_id']} tranzakciója lejárt ({timeout} mp)", "info")
                return None
            return tx
    return None

def add_purchased_balance(user_id: int, quantity: int):
    uid = str(user_id)
    limits = load_limits()
    if uid not in limits:
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0
        }
    user_data = limits[uid]
    user_data["purchased_balance"] = user_data.get("purchased_balance", 0) + quantity
    user_data["active_transaction"] = None
    save_limits(limits)
    log(f"+{quantity} lehetőség hozzáadva {user_id} fiókjához. Egyenleg: {user_data['purchased_balance']}", "ok")

def get_user_quota_info(user_id: int) -> dict:
    is_admin = user_id in config.ADMIN_IDS
    uid = str(user_id)
    limits = load_limits()
    user_data = limits.get(uid, {}) if isinstance(limits.get(uid), dict) else {}
    
    purchased = user_data.get("purchased_balance", 0)
    today_str = date.today().isoformat()
    free_date = user_data.get("free_completed_date")
    free_count = user_data.get("free_completed_count", 0)
    
    free_available = 1 if (free_date != today_str or free_count < 1) else 0
    total_completed = user_data.get("total_completed", 0)
    
    if is_admin and getattr(config, 'ADMIN_BYPASS_LIMIT', True):
        quota_str = "👑 Végtelen (Admin)"
        if purchased > 0:
            quota_str += f" • {purchased:02d} tartalék lehetőség"
    else:
        if purchased > 0:
            quota_str = f"{purchased:02d} megvásárolt lehetőség"
            if free_available > 0:
                quota_str += " + 01 ingyenes lehetőség ma"
        elif free_available > 0:
            quota_str = "01 ingyenes lehetőség ma"
        else:
            quota_str = "00 lehetőség (A mai ingyenes kvóta elfogyott)"

    return {
        "is_admin": is_admin,
        "purchased_balance": purchased,
        "free_available": free_available,
        "free_completed_count": free_count if free_date == today_str else 0,
        "total_completed": total_completed,
        "quota_str": quota_str
    }

async def fetch_sepay_transactions() -> list:
    api_key = config.SEPAY_API_KEY
    if not api_key:
        return []

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    endpoints = [
        "https://userapi.sepay.vn/v2/transactions?limit=50",
        "https://my.sepay.vn/userapi/transactions/list?limit=50"
    ]
    for url in endpoints:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        tx_list = data.get("data") if isinstance(data.get("data"), list) else data.get("transactions", [])
                        return tx_list or []
                    log(f"Fizetési API ({url.split('?')[0]}) válasz státusz: {r.status}", "warn")
        except Exception as e:
            log(f"Hiba a fizetési végpont ellenőrzésekor ({url}): {e}", "warn")
    return []

def match_payment(tx_list: list, order_id: str, expected_amount: float) -> bool:
    for tx in tx_list:
        content = tx.get("transaction_content", "") or tx.get("content", "") or ""
        try:
            amount = float(tx.get("amount_in", 0) or 0)
        except (ValueError, TypeError):
            amount = 0.0
        if order_id.lower() in content.lower() and amount >= expected_amount:
            return True
    return False

async def verify_payment_on_sepay(order_id: str, expected_amount: float) -> bool:
    if not config.SEPAY_API_KEY:
        log("Fizetési API kulcs nincs megadva, nem lehet ellenőrizni", "error")
        return False
    tx_list = await fetch_sepay_transactions()
    if match_payment(tx_list, order_id, expected_amount):
        log(f"Sikeres fizetés ellenőrzés a következő rendeléshez: {order_id}", "ok")
        return True
    return False

def get_pending_transactions() -> list:
    limits = load_limits()
    now = int(time.time())
    timeout = limits.get("transaction_timeout", 300)
    pending = []
    for uid, data in limits.items():
        if isinstance(data, dict) and data.get("active_transaction"):
            tx = data["active_transaction"]
            if now - tx.get("created_at", 0) <= timeout:
                pending.append((int(uid), tx))
    return pending

def complete_transaction(user_id: int, order_id: str) -> Optional[dict]:
    uid = str(user_id)
    limits = load_limits()
    user_data = limits.get(uid)
    if not isinstance(user_data, dict):
        return None
    tx = user_data.get("active_transaction")
    if not tx or tx.get("order_id") != order_id:
        return None
    user_data["purchased_balance"] = user_data.get("purchased_balance", 0) + tx["quests_to_buy"]
    user_data["active_transaction"] = None
    payments = user_data.setdefault("payments", [])
    payments.append({
        "order_id": order_id,
        "amount": tx["amount"],
        "quests": tx["quests_to_buy"],
        "paid_at": int(time.time())
    })
    del payments[:-20]
    save_limits(limits)
    log(f"+{tx['quests_to_buy']} lehetőség hozzáadva {user_id} fiókjához (rendelés: {order_id}). Egyenleg: {user_data['purchased_balance']}", "ok")
    return tx

def get_payment_history(user_id: int) -> list:
    limits = load_limits()
    data = limits.get(str(user_id))
    return list(data.get("payments", [])) if isinstance(data, dict) else []

def get_leaderboard(top: int = 10) -> list:
    limits = load_limits()
    rows = [(int(uid), d.get("total_completed", 0)) for uid, d in limits.items()
            if isinstance(d, dict) and uid.isdigit() and d.get("total_completed", 0) > 0]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:top]

def increment_guild_completed(guild_id):
    if not guild_id:
        return
    limits = load_limits()
    stats = limits.setdefault("guild_completed", {})
    stats[str(guild_id)] = stats.get(str(guild_id), 0) + 1
    save_limits(limits)

def get_guild_completed(guild_id) -> int:
    if not guild_id:
        return 0
    return int(load_limits().get("guild_completed", {}).get(str(guild_id), 0))

def save_global_stats():
    limits = load_limits()
    save_limits(limits)

def increment_user_total_completed(user_id: int):
    uid = str(user_id)
    limits = load_limits()
    if uid not in limits:
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0,
            "total_completed": 0,
            "active_transaction": None
        }
    elif not isinstance(limits[uid], dict):
        limits[uid] = {
            "free_completed_date": "",
            "free_completed_count": 0,
            "purchased_balance": 0,
            "total_completed": 0,
            "active_transaction": None
        }
    
    user_data = limits[uid]
    user_data["total_completed"] = user_data.get("total_completed", 0) + 1
    save_limits(limits)
    log(f"{user_id} összes teljesített küldetése növelve: {user_data['total_completed']}", "ok")

def get_user_total_completed(user_id: int) -> int:
    uid = str(user_id)
    limits = load_limits()
    if uid in limits and isinstance(limits[uid], dict):
        return limits[uid].get("total_completed", 0)
    return 0
