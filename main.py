import asyncio
import logging
import os
import sqlite3
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup, Message

API_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "takurwa")

H1_API_URL   = os.getenv("H1_API_URL", "http://lt1.h1cloud.net:25392/api")
H1_API_TOKEN = os.environ["H1_API_TOKEN"]
H1_INBOUND   = os.getenv("H1_INBOUND", "custom-vless-25393")

router = Router()


# ───────────────────────── Глобальный обработчик ошибок ─────────────────────────

@router.errors()
async def error_handler(event: ErrorEvent):
    exception = event.exception
    if isinstance(exception, TelegramForbiddenError):
        logging.warning("Bot blocked by user, skipping.")
        return True
    if isinstance(exception, TelegramBadRequest):
        logging.warning(f"Bad request: {exception}")
        return True
    logging.error(f"Unhandled error: {exception}")
    return False


# ───────────────────────── Имя клиента ─────────────────────────

def make_client_name(user_id: int) -> str:
    return f"u{user_id}"


# ───────────────────────── H1VLESS API ─────────────────────────

async def h1_get_client(user_id: int) -> dict | None:
    headers = {"Authorization": f"Bearer {H1_API_TOKEN}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{H1_API_URL}/clients",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        clients        = data.get("clients", [])
        possible_names = [f"u{user_id}", f"flex_{user_id}"]

        for client in clients:
            if client.get("name") in possible_names:
                return client

        return None

    except Exception as e:
        logging.error(f"H1 get client exception: {e}")
        return None


async def h1_create_client(user_id: int, days: int, devices: int) -> tuple[str | None, str | None]:
    headers = {
        "Authorization": f"Bearer {H1_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    payload = {
        "inboundTag": H1_INBOUND,
        "name":       make_client_name(user_id),
        "days":       days,
        "ip_limit":   devices,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{H1_API_URL}/clients",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                logging.info(f"H1 create client response {resp.status}: {text}")

                if resp.status == 409:
                    existing = await h1_get_client(user_id)
                    if existing:
                        uuid      = existing.get("uuid", "")
                        links     = existing.get("inbound_links", [])
                        vless_key = links[0].get("link", "") if links else None
                        sub_link  = (
                            existing.get("sub_url") or
                            existing.get("sub_link") or
                            (f"https://vpn.mayli.online/sub/{uuid}" if uuid else None)
                        )
                        return vless_key, sub_link
                    return None, None

                if resp.status not in (200, 201):
                    logging.error(f"H1 create client error {resp.status}: {text}")
                    return None, None

                data = await resp.json()

        client        = data.get("client", {})
        uuid          = client.get("uuid", "")
        inbound_links = client.get("inbound_links", [])
        vless_key     = inbound_links[0].get("link", "") if inbound_links else None
        sub_link      = (
            client.get("sub_url") or
            client.get("sub_link") or
            client.get("subscription_link") or
            client.get("subLink") or
            (f"https://vpn.mayli.online/sub/{uuid}" if uuid else None)
        )

        logging.info(f"Created. VLESS: {vless_key}, Sub: {sub_link}")
        return vless_key, sub_link

    except Exception as e:
        logging.error(f"H1 API exception: {e}")
        return None, None


async def h1_extend_client(user_id: int, days: int) -> tuple[str | None, str | None]:
    headers = {
        "Authorization": f"Bearer {H1_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    try:
        client = await h1_get_client(user_id)
        if not client:
            return None, None
        uuid = client.get("uuid")
        if not uuid:
            return None, None

        left_days  = client.get("left_days", 0)
        if left_days < 0:
            left_days = 0
        total_days = left_days + days
        logging.info(f"Extending {uuid}: left={left_days} + new={days} = total={total_days}")

        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{H1_API_URL}/clients/{uuid}",
                json={"days": total_days},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                logging.info(f"H1 extend response {resp.status}: {text}")

                if resp.status == 404:
                    logging.warning(f"Client {uuid} not found on extend, will recreate")
                    return None, None

                if resp.status not in (200, 201, 204):
                    return None, None

                try:
                    resp_data = await resp.json()
                except Exception:
                    resp_data = {}

        updated   = resp_data.get("client", {})
        links     = updated.get("inbound_links", [])
        vless_key = links[0].get("link", "") if links else None

        if not vless_key:
            existing_links = client.get("inbound_links", [])
            vless_key = existing_links[0].get("link", "") if existing_links else None

        sub_link = (
            updated.get("sub_url") or
            updated.get("sub_link") or
            client.get("sub_url") or
            client.get("sub_link") or
            f"https://vpn.mayli.online/sub/{uuid}"
        )

        return vless_key, sub_link

    except Exception as e:
        logging.error(f"H1 extend client exception: {e}")
        return None, None


async def h1_disable_client(user_id: int) -> bool:
    headers = {
        "Authorization": f"Bearer {H1_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    try:
        client = await h1_get_client(user_id)
        if not client:
            return False
        uuid = client.get("uuid")
        if not uuid:
            return False
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{H1_API_URL}/clients/{uuid}",
                json={"enabled": False},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 201, 204)
    except Exception as e:
        logging.error(f"H1 disable client exception: {e}")
        return False


async def h1_update_devices(user_id: int, devices: int) -> bool:
    headers = {
        "Authorization": f"Bearer {H1_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    try:
        client = await h1_get_client(user_id)
        if not client:
            return False
        uuid = client.get("uuid")
        if not uuid:
            return False
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{H1_API_URL}/clients/{uuid}",
                json={"ip_limit": devices},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 201, 204)
    except Exception as e:
        logging.error(f"H1 update devices exception: {e}")
        return False


async def h1_delete_client(user_id: int) -> bool:
    headers = {"Authorization": f"Bearer {H1_API_TOKEN}"}
    try:
        client = await h1_get_client(user_id)
        if not client:
            return False
        uuid = client.get("uuid")
        if not uuid:
            return False
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{H1_API_URL}/clients/{uuid}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 204)
    except Exception as e:
        logging.error(f"H1 delete client exception: {e}")
        return False


async def h1_get_or_create_client(user_id: int, days: int, devices: int) -> tuple[str | None, str | None]:
    existing = await h1_get_client(user_id)
    if existing:
        uuid                = existing.get("uuid", "")
        vless_key, sub_link = await h1_extend_client(user_id, days)

        if vless_key or sub_link:
            if not sub_link:
                sub_link = (
                    existing.get("sub_url") or
                    f"https://vpn.mayli.online/sub/{uuid}"
                )
            return vless_key, sub_link

        logging.warning(f"Extend failed for {user_id}, recreating...")
        await h1_delete_client(user_id)

    return await h1_create_client(user_id, days, devices)


def calc_expire_date(user_id: int, days: int) -> datetime:
    user = get_user(user_id)
    if user and user["sub_expire"]:
        try:
            current_expire = datetime.strptime(user["sub_expire"], "%d.%m.%Y %H:%M")
            if current_expire > datetime.now():
                return current_expire + timedelta(days=days)
        except Exception:
            pass
    return datetime.now() + timedelta(days=days)


# ───────────────────────── БД ─────────────────────────

def init_db():
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            balance     INTEGER DEFAULT 0,
            refs        INTEGER DEFAULT 0,
            ref_trials  INTEGER DEFAULT 0,
            ref_earned  INTEGER DEFAULT 0,
            sub_name    TEXT,
            sub_expire  TEXT,
            sub_link    TEXT,
            agreed      INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT NULL,
            main_msg_id INTEGER DEFAULT NULL,
            sub_url     TEXT DEFAULT NULL,
            notified_3d INTEGER DEFAULT 0,
            notified_0d INTEGER DEFAULT 0
        )
    """)
    for col, definition in [
        ("agreed",      "INTEGER DEFAULT 0"),
        ("referrer_id", "INTEGER DEFAULT NULL"),
        ("main_msg_id", "INTEGER DEFAULT NULL"),
        ("sub_url",     "TEXT DEFAULT NULL"),
        ("notified_3d", "INTEGER DEFAULT 0"),
        ("notified_0d", "INTEGER DEFAULT 0"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promocodes (
            code      TEXT PRIMARY KEY,
            amount    INTEGER,
            uses_left INTEGER
        )
    """)

    # Таблица использованных промокодов (один юзер = один раз)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_usage (
            user_id INTEGER,
            code    TEXT,
            PRIMARY KEY (user_id, code)
        )
    """)

    con.commit()
    con.close()


def get_user(user_id: int) -> dict | None:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return {
            "user_id":     row[0],
            "username":    row[1],
            "balance":     row[2],
            "refs":        row[3],
            "ref_trials":  row[4],
            "ref_earned":  row[5],
            "sub_name":    row[6],
            "sub_expire":  row[7],
            "sub_link":    row[8],
            "agreed":      row[9]  if len(row) > 9  else 0,
            "referrer_id": row[10] if len(row) > 10 else None,
            "main_msg_id": row[11] if len(row) > 11 else None,
            "sub_url":     row[12] if len(row) > 12 else None,
            "notified_3d": row[13] if len(row) > 13 else 0,
            "notified_0d": row[14] if len(row) > 14 else 0,
        }
    return None


def get_all_users() -> list:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    con.close()
    result = []
    for row in rows:
        result.append({
            "user_id":     row[0],
            "username":    row[1],
            "balance":     row[2],
            "refs":        row[3],
            "ref_trials":  row[4],
            "ref_earned":  row[5],
            "sub_name":    row[6],
            "sub_expire":  row[7],
            "sub_link":    row[8],
            "agreed":      row[9]  if len(row) > 9  else 0,
            "referrer_id": row[10] if len(row) > 10 else None,
            "main_msg_id": row[11] if len(row) > 11 else None,
            "sub_url":     row[12] if len(row) > 12 else None,
            "notified_3d": row[13] if len(row) > 13 else 0,
            "notified_0d": row[14] if len(row) > 14 else 0,
        })
    return result


def get_stats() -> dict:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE sub_name IS NOT NULL AND sub_expire IS NOT NULL")
    active_subs = cur.fetchone()[0]
    cur.execute("SELECT SUM(ref_earned) FROM users")
    total_earned = cur.fetchone()[0] or 0
    con.close()
    return {
        "total_users":  total_users,
        "active_subs":  active_subs,
        "total_earned": total_earned,
    }


def has_used_promo(user_id: int, code: str) -> bool:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT 1 FROM promo_usage WHERE user_id=? AND code=?", (user_id, code))
    result = cur.fetchone()
    con.close()
    return result is not None


def mark_promo_used(user_id: int, code: str):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO promo_usage (user_id, code) VALUES (?,?)",
        (user_id, code),
    )
    con.commit()
    con.close()


def set_notified(user_id: int, field: str):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(f"UPDATE users SET {field}=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def reset_notifications(user_id: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET notified_3d=0, notified_0d=0 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def ensure_user(user_id: int, username: str = "", referrer_id: int = None) -> bool:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    exists = cur.fetchone()
    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, username, agreed, referrer_id) VALUES (?,?,0,?)",
            (user_id, username or "", referrer_id),
        )
        con.commit()
        con.close()
        return True
    cur.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", user_id))
    con.commit()
    con.close()
    return False


def set_main_msg_id(user_id: int, msg_id: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET main_msg_id=? WHERE user_id=?", (msg_id, user_id))
    con.commit()
    con.close()


def set_agreed(user_id: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET agreed=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def set_balance(user_id: int, balance: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (balance, user_id))
    con.commit()
    con.close()


def increment_refs(referrer_id: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (referrer_id,))
    con.commit()
    con.close()


def add_ref_earned(referrer_id: int, amount: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET ref_earned = ref_earned + ?, balance = balance + ? WHERE user_id=?",
        (amount, amount, referrer_id),
    )
    con.commit()
    con.close()


def set_sub(user_id: int, name: str, expire: str, link: str, sub_url: str = None):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET sub_name=?, sub_expire=?, sub_link=?, sub_url=? WHERE user_id=?",
        (name, expire, link, sub_url, user_id),
    )
    con.commit()
    con.close()


def clear_sub(user_id: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET sub_name=NULL, sub_expire=NULL, sub_link=NULL, sub_url=NULL WHERE user_id=?",
        (user_id,),
    )
    con.commit()
    con.close()


def get_promo(code: str) -> dict | None:
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("SELECT code, amount, uses_left FROM promocodes WHERE code=?", (code,))
    row = cur.fetchone()
    con.close()
    if row:
        return {"code": row[0], "amount": row[1], "uses_left": row[2]}
    return None


def create_promo(code: str, amount: int, uses: int):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO promocodes (code, amount, uses_left) VALUES (?,?,?)",
        (code, amount, uses),
    )
    con.commit()
    con.close()


def use_promo(code: str):
    con = sqlite3.connect("flexvpn.db")
    cur = con.cursor()
    cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code=?", (code,))
    cur.execute("DELETE FROM promocodes WHERE uses_left <= 0")
    con.commit()
    con.close()


def find_user_by_username(username: str) -> dict | None:
    clean = username.lstrip("@").strip()
    con   = sqlite3.connect("flexvpn.db")
    cur   = con.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (clean,))
    row = cur.fetchone()
    con.close()
    if row:
        return {
            "user_id":    row[0],
            "username":   row[1],
            "balance":    row[2],
            "sub_name":   row[6],
            "sub_expire": row[7],
        }
    return None


# ───────────────────────── FSM ─────────────────────────

class Form(StatesGroup):
    topup_amount        = State()
    promo_code          = State()
    admin_give_username = State()
    admin_give_amount   = State()
    admin_promo_code    = State()
    admin_promo_amount  = State()
    admin_promo_uses    = State()
    admin_sub_username  = State()
    admin_sub_days      = State()
    admin_sub_devices   = State()
    admin_dev_username  = State()
    admin_dev_count     = State()
    admin_del_username  = State()


# ───────────────────────── Хелперы ─────────────────────────

def btn(text: str, cbd: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cbd)


def format_key_text(vless_key: str | None, sub_link: str | None) -> str:
    if sub_link:
        return (
            f"🔗 *Ссылка на подписку:*\n"
            f"`{sub_link}`\n\n"
            f"📲 Как добавить:\n"
            f"• *Hiddify* → + → Вставить ссылку\n"
            f"• *V2RayTun* → + → Импорт по ссылке\n"
            f"• *Streisand* → + → Вставить URL\n"
            f"• *V2Box* → + → Импорт из буфера"
        )
    elif vless_key:
        return (
            f"🔑 *Ваш ключ подключения:*\n"
            f"`{vless_key}`\n\n"
            f"📲 Как подключиться:\n"
            f"• *Hiddify* → + → Добавить ссылку\n"
            f"• *V2RayTun* → + → Импорт из буфера\n"
            f"• *Streisand* → + → Вставить\n"
            f"• *V2Box* → + → Вставить ссылку"
        )
    return "❌ Ключ не получен. Обратитесь в поддержку: @takurwa"


def is_admin(user) -> bool:
    return (user.username or "").lower() == ADMIN_USERNAME.lower()


async def safe_delete(bot: Bot, chat_id: int, msg_id: int):
    """Безопасно удаляет сообщение"""
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def safe_edit(message, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Безопасно редактирует сообщение"""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception:
        return False


# ───────────────────────── Клавиатуры ─────────────────────────

def get_welcome_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Политика конфиденциальности ↗", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [InlineKeyboardButton(text="📋 Пользовательское соглашение ↗", url="https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15")],
        [InlineKeyboardButton(text="💰 Условия возврата ↗",            url="https://t.me/takurwa")],
        [btn("✅ Я ознакомлен и принимаю условия", "agree")],
    ])


def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🛒 Купить подписку", "buy_sub")],
        [btn("📊 Моя подписка",    "my_sub")],
        [btn("💰 Баланс", "balance"), btn("🎫 Промокод", "promocode")],
        [btn("👥 Пригласить друзей", "ref")],
        [InlineKeyboardButton(text="🆘 Поддержка ↗", url="https://t.me/takurwa")],
        [btn("📄 Документы", "docs")],
    ])


def get_devices_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("📱 3 устройства",  "devices_3")],
        [btn("📱📱 6 устройств", "devices_6")],
        [btn("◀️ Назад",        "main_menu")],
    ])


def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("💸 Выдать деньги",      "admin_give")],
        [btn("🎫 Создать промокод",    "admin_create_promo")],
        [btn("✅ Выдать подписку",     "admin_give_sub")],
        [btn("❌ Удалить подписку",    "admin_del_sub")],
        [btn("📱 Изменить устройства", "admin_change_dev")],
        [btn("📊 Статистика",         "admin_stats")],
        [btn("◀️ Назад в меню",       "main_menu")],
    ])


def get_docs_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Политика конфиденциальности ↗", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [InlineKeyboardButton(text="📋 Пользовательское соглашение ↗", url="https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15")],
        [InlineKeyboardButton(text="💰 Условия возврата ↗",            url="https://t.me/takurwa")],
        [btn("🔒 Политика бота", "bot_policy")],
        [btn("◀️ Назад в меню",  "main_menu")],
    ])


TARIFFS = {
    "3_1d": ("1 день (пробный)",     29,   1, 3),
    "3_1m": ("1 месяц",              99,  30, 3),
    "3_3m": ("3 месяца",            219,  90, 3),
    "3_6m": ("6 месяцев",           449, 180, 3),
    "3_1y": ("1 год",               739, 365, 3),
    "6_1m": ("1 месяц (6 устр.)",   119,  30, 6),
    "6_3m": ("3 месяца (6 устр.)",  239,  90, 6),
    "6_6m": ("6 месяцев (6 устр.)", 479, 180, 6),
    "6_1y": ("1 год (6 устр.)",     849, 365, 6),
}

REF_BONUS_PERCENT = 20

MAIN_TEXT = (
    "💎 *FLEX VPN*\n\n"
    "🌐 От 5 серверов в разных странах\n"
    "🛡 Без логов подключений\n"
    "🔒 Надёжное подключение\n"
    "🚀 Высокая скорость соединения"
)


# ───────────────────────── Проверка подписок ─────────────────────────

async def check_subscriptions(bot: Bot):
    while True:
        try:
            users = get_all_users()
            now   = datetime.now()

            for user in users:
                if not user["sub_expire"]:
                    continue

                try:
                    expire = datetime.strptime(user["sub_expire"], "%d.%m.%Y %H:%M")
                except ValueError:
                    continue

                days_left = (expire - now).days
                user_id   = user["user_id"]

                if days_left == 3 and not user["notified_3d"]:
                    try:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [btn("🔄 Продлить подписку", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"⚠️ *Ваша подписка истекает через 3 дня!*\n\n"
                            f"📅 Дата окончания: *{expire.strftime('%d.%m.%Y')}*\n\n"
                            f"Продлите подписку чтобы не потерять доступ.",
                            reply_markup=kb,
                            parse_mode="Markdown",
                        )
                        set_notified(user_id, "notified_3d")
                    except TelegramForbiddenError:
                        logging.warning(f"User {user_id} blocked bot.")
                    except Exception as e:
                        logging.error(f"Notify 3d error for {user_id}: {e}")

                elif days_left == 0 and not user["notified_0d"]:
                    try:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [btn("🔄 Продлить подписку", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"🔴 *Ваша подписка истекает сегодня!*\n\n"
                            f"Продлите прямо сейчас чтобы не потерять доступ.",
                            reply_markup=kb,
                            parse_mode="Markdown",
                        )
                        set_notified(user_id, "notified_0d")
                    except TelegramForbiddenError:
                        logging.warning(f"User {user_id} blocked bot.")
                    except Exception as e:
                        logging.error(f"Notify 0d error for {user_id}: {e}")

                elif days_left < 0:
                    try:
                        await h1_disable_client(user_id)
                        clear_sub(user_id)
                        reset_notifications(user_id)
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [btn("🛒 Купить подписку", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"❌ *Ваша подписка истекла!*\n\n"
                            f"Доступ к VPN отключён.\n"
                            f"Оформите новую подписку чтобы продолжить.",
                            reply_markup=kb,
                            parse_mode="Markdown",
                        )
                    except TelegramForbiddenError:
                        logging.warning(f"User {user_id} blocked bot.")
                        clear_sub(user_id)
                        reset_notifications(user_id)
                    except Exception as e:
                        logging.error(f"Disable sub error for {user_id}: {e}")

        except Exception as e:
            logging.error(f"check_subscriptions error: {e}")

        await asyncio.sleep(3600)


# ───────────────────────── Хендлеры ─────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
    user_id  = message.from_user.id
    username = message.from_user.username or ""
    args     = command.args

    try:
        await message.delete()
    except Exception:
        pass

    referrer_id = None
    if args and args.isdigit():
        ref_id = int(args)
        if ref_id != user_id:
            referrer = get_user(ref_id)
            if referrer:
                referrer_id = ref_id

    is_new = ensure_user(user_id, username, referrer_id)
    if is_new and referrer_id:
        increment_refs(referrer_id)

    user = get_user(user_id)

    if user["main_msg_id"]:
        await safe_delete(bot, message.chat.id, user["main_msg_id"])

    try:
        if not user["agreed"]:
            msg = await message.answer(
                "👋 *Добро пожаловать в FLEX VPN!*\n\n"
                "Перед началом работы ознакомьтесь с документами сервиса.\n\n"
                "📄 Прочитайте:\n"
                "• Политику конфиденциальности\n"
                "• Пользовательское соглашение\n"
                "• Условия возврата\n\n"
                "После ознакомления нажмите кнопку ниже 👇",
                reply_markup=get_welcome_menu(),
                parse_mode="Markdown",
            )
            set_main_msg_id(user_id, msg.message_id)
            return

        msg = await message.answer(MAIN_TEXT, reply_markup=get_main_menu(), parse_mode="Markdown")
        set_main_msg_id(user_id, msg.message_id)
    except TelegramForbiddenError:
        logging.warning(f"User {user_id} blocked bot.")
    except Exception as e:
        logging.error(f"cmd_start error: {e}")


@router.message(Command("admin"))
async def admin_cmd(message: Message, bot: Bot):
    try:
        await message.delete()
    except Exception:
        pass
    if not is_admin(message.from_user):
        return

    user_id = message.from_user.id
    user    = get_user(user_id)

    if user and user["main_msg_id"]:
        await safe_delete(bot, message.chat.id, user["main_msg_id"])

    try:
        msg = await message.answer(
            "👑 *Админ-панель FLEX VPN*",
            reply_markup=get_admin_menu(),
            parse_mode="Markdown",
        )
        set_main_msg_id(user_id, msg.message_id)
    except Exception as e:
        logging.error(f"admin_cmd error: {e}")


@router.message(Command("stats"))
async def stats_cmd(message: Message, bot: Bot):
    try:
        await message.delete()
    except Exception:
        pass
    if not is_admin(message.from_user):
        return

    stats   = get_stats()
    user_id = message.from_user.id
    user    = get_user(user_id)
    text    = (
        f"📊 *Статистика FLEX VPN*\n\n"
        f"👤 Всего пользователей: *{stats['total_users']}*\n"
        f"✅ Активных подписок: *{stats['active_subs']}*\n"
        f"💰 Выплачено рефералам: *{stats['total_earned']}₽*"
    )

    # Удаляем старое сообщение и отправляем новое
    if user and user["main_msg_id"]:
        await safe_delete(bot, message.chat.id, user["main_msg_id"])

    try:
        msg = await bot.send_message(
            message.chat.id,
            text,
            reply_markup=get_admin_menu(),
            parse_mode="Markdown",
        )
        set_main_msg_id(user_id, msg.message_id)
    except Exception as e:
        logging.error(f"stats_cmd error: {e}")


@router.callback_query(F.data == "admin_stats")
async def admin_stats_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    stats = get_stats()
    text  = (
        f"📊 *Статистика FLEX VPN*\n\n"
        f"👤 Всего пользователей: *{stats['total_users']}*\n"
        f"✅ Активных подписок: *{stats['active_subs']}*\n"
        f"💰 Выплачено рефералам: *{stats['total_earned']}₽*"
    )
    await safe_edit(callback.message, text, reply_markup=get_admin_menu())
    await callback.answer()


@router.callback_query(F.data == "agree")
async def agree_cb(callback: CallbackQuery):
    ensure_user(callback.from_user.id, callback.from_user.username or "")
    set_agreed(callback.from_user.id)
    await safe_edit(callback.message, MAIN_TEXT, reply_markup=get_main_menu())
    await callback.answer("✅ Добро пожаловать!")


@router.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery):
    await safe_edit(callback.message, MAIN_TEXT, reply_markup=get_main_menu())
    await callback.answer()


@router.callback_query(F.data == "buy_sub")
async def buy_sub_cb(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "📱 *Выберите количество устройств*\n\n"
        "Подписка будет работать одновременно на выбранном числе устройств.",
        reply_markup=get_devices_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.in_(["devices_3", "devices_6"]))
async def select_devices_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user    = get_user(user_id)
    balance = user["balance"]
    dev     = 3 if callback.data == "devices_3" else 6

    if dev == 3:
        text = (
            f"📅 *Выберите период подписки*\n\n"
            f"💰 Ваш баланс: *{balance}₽*\n"
            f"📱 Устройств: *3*\n"
            f"📊 Трафик: *150 ГБ/мес*"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("🟢 1 день (пробный) — 29₽", "tar_3_1d")],
            [btn("🔵 1 месяц — 99₽",           "tar_3_1m")],
            [btn("🟣 3 месяца — 219₽",         "tar_3_3m")],
            [btn("🟠 6 месяцев — 449₽",        "tar_3_6m")],
            [btn("🏆 1 год — 739₽",            "tar_3_1y")],
            [btn("◀️ Назад",                   "buy_sub")],
        ])
    else:
        text = (
            f"📅 *Выберите период подписки*\n\n"
            f"💰 Ваш баланс: *{balance}₽*\n"
            f"📱 Устройств: *6*\n"
            f"📊 Трафик: *300 ГБ/мес*"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔵 1 месяц — 119₽",   "tar_6_1m")],
            [btn("🟣 3 месяца — 239₽",  "tar_6_3m")],
            [btn("🟠 6 месяцев — 479₽", "tar_6_6m")],
            [btn("🏆 1 год — 849₽",     "tar_6_1y")],
            [btn("◀️ Назад",            "buy_sub")],
        ])

    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("tar_"))
async def process_tariff_cb(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")

    parts       = callback.data.split("_")
    tariff_code = parts[1] + "_" + parts[2]

    if tariff_code not in TARIFFS:
        await callback.answer("❌ Неизвестный тариф.", show_alert=True)
        return

    name, price, days, devices = TARIFFS[tariff_code]
    user    = get_user(user_id)
    balance = user["balance"]

    if balance < price:
        await callback.answer(
            f"❌ Недостаточно средств!\nНужно {price}₽, у вас {balance}₽.",
            show_alert=True,
        )
        return

    await callback.answer("⏳ Создаём подписку...")
    await safe_edit(
        callback.message,
        "⏳ *Создаём вашу подписку...*\n\nПожалуйста подождите.",
    )

    vless_key, sub_link = await h1_get_or_create_client(user_id, days, devices)

    if not vless_key and not sub_link:
        await safe_edit(
            callback.message,
            "❌ Ошибка при создании подписки.\nОбратитесь в поддержку: @takurwa",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("◀️ Назад в меню", "main_menu")]]),
        )
        return

    new_balance = balance - price
    expire_date = calc_expire_date(user_id, days)
    is_renewal  = user.get("sub_name") is not None

    set_balance(user_id, new_balance)
    set_sub(user_id, name, expire_date.strftime("%d.%m.%Y %H:%M"), vless_key or "", sub_link)
    reset_notifications(user_id)

    referrer_id = user.get("referrer_id")
    if referrer_id:
        bonus = int(price * REF_BONUS_PERCENT / 100)
        if bonus > 0:
            add_ref_earned(referrer_id, bonus)
            try:
                await bot.send_message(
                    referrer_id,
                    f"🎉 По вашей реферальной ссылке совершена покупка!\n"
                    f"💰 Вам начислено *{bonus}₽* ({REF_BONUS_PERCENT}% от {price}₽)",
                    parse_mode="Markdown",
                )
            except TelegramForbiddenError:
                pass
            except Exception as e:
                logging.error(f"Ref bonus notify error: {e}")

    key_text = format_key_text(vless_key, sub_link)
    title    = "🔄 *Подписка продлена!*" if is_renewal else "✅ *Подписка активирована!*"

    text = (
        f"{title}\n\n"
        f"📅 {name}\n"
        f"📱 Устройств: *{devices}*\n"
        f"⏳ Действует до: *{expire_date.strftime('%d.%m.%Y')}*\n"
        f"💰 Списано: *{price}₽*\n"
        f"💳 Остаток: *{new_balance}₽*\n\n"
        f"{key_text}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("📋 Скопировать ключ", "copy_key")],
        [btn("◀️ Назад в меню",    "main_menu")],
    ])
    await safe_edit(callback.message, text, reply_markup=kb)


@router.callback_query(F.data == "my_sub")
async def my_sub_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user = get_user(user_id)

    if not user["sub_name"]:
        text = (
            "📊 *Моя подписка*\n\n"
            "У вас пока нет активной подписки.\n"
            "Оформите её в разделе «Купить подписку»."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("🛒 Купить подписку", "buy_sub")],
            [btn("◀️ Назад в меню",   "main_menu")],
        ])
    else:
        try:
            expire    = datetime.strptime(user["sub_expire"], "%d.%m.%Y %H:%M")
            days_left = (expire - datetime.now()).days
            days_text = f"⏳ Осталось: *{days_left} дн.*\n" if days_left >= 0 else "🔴 Подписка истекла\n"
        except Exception:
            days_text = ""

        key_text = format_key_text(user.get("sub_link"), user.get("sub_url"))
        text = (
            f"📊 *Моя подписка*\n\n"
            f"📅 Тариф: *{user['sub_name']}*\n"
            f"📆 До: *{user['sub_expire']}*\n"
            f"{days_text}\n"
            f"{key_text}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("📋 Скопировать ключ",  "copy_key")],
            [btn("🔄 Продлить подписку", "buy_sub")],
            [btn("◀️ Назад в меню",     "main_menu")],
        ])

    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "copy_key")
async def copy_key_cb(callback: CallbackQuery):
    await callback.answer("Ключ скопирован в буфер обмена!", show_alert=True)


@router.callback_query(F.data == "balance")
async def balance_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user = get_user(user_id)
    kb   = InlineKeyboardMarkup(inline_keyboard=[
        [btn("💳 Пополнить баланс", "topup")],
        [btn("◀️ Назад в меню",    "main_menu")],
    ])
    await safe_edit(callback.message, f"💰 Ваш баланс: *{user['balance']}₽*", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "topup")
async def topup_cb(callback: CallbackQuery, state: FSMContext):
    await safe_edit(
        callback.message,
        "💳 *Введите сумму пополнения в рублях (например: 100):*\n\nИли нажмите /start чтобы отменить.",
    )
    await state.set_state(Form.topup_amount)
    await callback.answer()


@router.message(Form.topup_amount)
async def process_topup_amount(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass
    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(user_id, "Введите положительное число:")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить ↗", url="https://t.me/durov")],
        [btn("◀️ Назад в меню", "main_menu")],
    ])
    user = get_user(user_id)
    text = f"💳 *Пополнение баланса на {amount}₽*\n\nНажмите «Оплатить» — баланс пополнится после оплаты."

    if user and user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=user["main_msg_id"],
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception:
            try:
                msg = await bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode="Markdown")
                set_main_msg_id(user_id, msg.message_id)
            except Exception as e:
                logging.error(f"topup send error: {e}")
    await state.clear()


@router.callback_query(F.data == "promocode")
async def promocode_cb(callback: CallbackQuery, state: FSMContext):
    await safe_edit(
        callback.message,
        "🎫 *Введите промокод сообщением:*\n\nИли нажмите /start чтобы отменить.",
    )
    await state.set_state(Form.promo_code)
    await callback.answer()


@router.message(Form.promo_code)
async def process_promo_code(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass

    code = message.text.strip().upper()
    user = get_user(user_id)

    if has_used_promo(user_id, code):
        result_text = "❌ Вы уже использовали этот промокод."
    else:
        promo = get_promo(code)
        if not promo:
            result_text = "❌ Промокод не найден или уже использован."
        else:
            ensure_user(user_id, message.from_user.username or "")
            set_balance(user_id, user["balance"] + promo["amount"])
            use_promo(code)
            mark_promo_used(user_id, code)
            result_text = f"✅ Промокод активирован!\n💰 Зачислено *{promo['amount']}₽*"

    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("◀️ Назад в меню", "main_menu")]])

    if user and user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                result_text,
                chat_id=message.chat.id,
                message_id=user["main_msg_id"],
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception:
            try:
                msg = await bot.send_message(
                    message.chat.id, result_text,
                    reply_markup=kb, parse_mode="Markdown",
                )
                set_main_msg_id(user_id, msg.message_id)
            except Exception as e:
                logging.error(f"promo send error: {e}")
    await state.clear()


@router.callback_query(F.data == "ref")
async def ref_cb(callback: CallbackQuery, bot: Bot):
    user_id  = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user     = get_user(user_id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    text = (
        f"👥 *Заработать с FLEX VPN*\n\n"
        f"Приглашай друзей и получай *{REF_BONUS_PERCENT}%* с их оплат на баланс.\n\n"
        f"👤 Друзей приглашено: *{user['refs']}*\n"
        f"🎯 Активировали пробный: *{user['ref_trials']}*\n"
        f"💵 Заработано: *{user['ref_earned']}₽*\n\n"
        f"🔗 *Ваша реферальная ссылка:*\n`{ref_link}`"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("◀️ Назад в меню", "main_menu")]])
    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "docs")
async def docs_cb(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "📄 *Документы FLEX VPN*\n\nВся документация сервиса всегда в открытом доступе.\n\n📌 Служба поддержки: @takurwa",
        reply_markup=get_docs_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "bot_policy")
async def bot_policy_cb(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡 Политика конфиденциальности ↗", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [btn("◀️ Назад", "docs")],
    ])
    await safe_edit(
        callback.message,
        "🔒 *Политика бота FLEX VPN*\n\n"
        "• Бот собирает только необходимые данные\n"
        "• Данные не передаются третьим лицам\n"
        "• Используются исключительно для предоставления услуг VPN\n"
        "• Запрос на удаление данных: @takurwa",
        reply_markup=kb,
    )
    await callback.answer()


# ───────────────────────── АДМИН ─────────────────────────

async def admin_ask(callback: CallbackQuery, state: FSMContext, text: str, next_state):
    if not is_admin(callback.from_user):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    await safe_edit(callback.message, text)
    await state.set_state(next_state)
    await callback.answer()


@router.callback_query(F.data == "admin_give")
async def admin_give_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "💸 Введите *@username* пользователя:", Form.admin_give_username)


@router.message(Form.admin_give_username)
async def admin_give_username_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    username   = message.text.strip().lstrip("@").strip()
    user       = find_user_by_username(username)
    admin_user = get_user(message.from_user.id)
    if not user:
        if admin_user and admin_user["main_msg_id"]:
            try:
                await bot.edit_message_text(
                    f"❌ Пользователь @{username} не найден.\n\nВведите другой username:",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return
    await state.update_data(target_username=username, target_id=user["user_id"])
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ Найден: @{username}\n💰 Баланс: *{get_user(user['user_id'])['balance']}₽*\n\nВведите сумму (отрицательная — снятие):",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_give_amount)


@router.message(Form.admin_give_amount)
async def admin_give_amount_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        amount = int(message.text.strip())
        if amount == 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите число (не ноль):")
        return
    data       = await state.get_data()
    target_id  = data["target_id"]
    target_un  = data["target_username"]
    user       = get_user(target_id)
    new_bal    = max(0, user["balance"] + amount)
    set_balance(target_id, new_bal)
    sign       = "+" if amount > 0 else ""
    admin_user = get_user(message.from_user.id)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ @{target_un}: {sign}{amount}₽\n💰 Новый баланс: *{new_bal}₽*",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data == "admin_give_sub")
async def admin_give_sub_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "✅ Введите *@username* пользователя которому выдать подписку:", Form.admin_sub_username)


@router.message(Form.admin_sub_username)
async def admin_sub_username_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    username   = message.text.strip().lstrip("@").strip()
    user       = find_user_by_username(username)
    admin_user = get_user(message.from_user.id)
    if not user:
        if admin_user and admin_user["main_msg_id"]:
            try:
                await bot.edit_message_text(
                    f"❌ Пользователь @{username} не найден.\n\nВведите другой username:",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return
    await state.update_data(target_username=username, target_id=user["user_id"])
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ Найден: @{username}\n\nВведите количество *дней* подписки:",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_sub_days)


@router.message(Form.admin_sub_days)
async def admin_sub_days_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите положительное число дней:")
        return
    await state.update_data(sub_days=days)
    admin_user = get_user(message.from_user.id)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"Дней: *{days}*\n\nВведите количество *устройств*:",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_sub_devices)


@router.message(Form.admin_sub_devices)
async def admin_sub_devices_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        devices = int(message.text.strip())
        if devices <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите положительное число:")
        return

    data       = await state.get_data()
    target_id  = data["target_id"]
    target_un  = data["target_username"]
    days       = data["sub_days"]
    admin_user = get_user(message.from_user.id)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                "⏳ Создаём подписку...",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
            )
        except Exception:
            pass

    vless_key, sub_link = await h1_get_or_create_client(target_id, days, devices)

    if not vless_key and not sub_link:
        if admin_user and admin_user["main_msg_id"]:
            try:
                await bot.edit_message_text(
                    "❌ Ошибка при создании подписки в H1VLESS.",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    reply_markup=get_admin_menu(),
                )
            except Exception:
                pass
        await state.clear()
        return

    expire_date = calc_expire_date(target_id, days)
    set_sub(target_id, f"Админ ({days}д, {devices}уст.)", expire_date.strftime("%d.%m.%Y %H:%M"), vless_key or "", sub_link)
    reset_notifications(target_id)
    key_text = format_key_text(vless_key, sub_link)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ Подписка выдана @{target_un}!\n\n"
                f"📅 Дней: *{days}*\n"
                f"📱 Устройств: *{devices}*\n"
                f"⏳ До: *{expire_date.strftime('%d.%m.%Y')}*\n\n"
                f"{key_text}",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data == "admin_del_sub")
async def admin_del_sub_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "❌ Введите *@username* пользователя у которого удалить подписку:", Form.admin_del_username)


@router.message(Form.admin_del_username)
async def admin_del_username_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    username   = message.text.strip().lstrip("@").strip()
    user       = find_user_by_username(username)
    admin_user = get_user(message.from_user.id)

    if not user:
        if admin_user and admin_user["main_msg_id"]:
            try:
                await bot.edit_message_text(
                    f"❌ Пользователь @{username} не найден.",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    reply_markup=get_admin_menu(),
                )
            except Exception:
                pass
        await state.clear()
        return

    target_id = user["user_id"]
    deleted   = await h1_delete_client(target_id)
    clear_sub(target_id)
    reset_notifications(target_id)
    status    = "✅ Удалено" if deleted else "⚠️ Клиент не найден в H1VLESS (БД очищена)"

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"{status}\n🗑 Подписка @{username} удалена.",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data == "admin_change_dev")
async def admin_change_dev_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "📱 Введите *@username* пользователя:", Form.admin_dev_username)


@router.message(Form.admin_dev_username)
async def admin_dev_username_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    username   = message.text.strip().lstrip("@").strip()
    user       = find_user_by_username(username)
    admin_user = get_user(message.from_user.id)
    if not user:
        if admin_user and admin_user["main_msg_id"]:
            try:
                await bot.edit_message_text(
                    f"❌ Пользователь @{username} не найден.\n\nВведите другой username:",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return
    await state.update_data(target_username=username, target_id=user["user_id"])
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ Найден: @{username}\n\nВведите новое количество устройств:",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_dev_count)


@router.message(Form.admin_dev_count)
async def admin_dev_count_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        devices = int(message.text.strip())
        if devices <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите положительное число:")
        return

    data       = await state.get_data()
    target_id  = data["target_id"]
    target_un  = data["target_username"]
    admin_user = get_user(message.from_user.id)
    success    = await h1_update_devices(target_id, devices)

    result = (
        f"✅ У @{target_un} установлен лимит *{devices}* устройств."
        if success else
        f"❌ Не удалось изменить — клиент не найден в H1VLESS."
    )

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                result,
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


@router.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "🎫 Введите *код* промокода (например: FLEX2024):", Form.admin_promo_code)


@router.message(Form.admin_promo_code)
async def admin_promo_code_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    code       = message.text.strip().upper()
    admin_user = get_user(message.from_user.id)
    await state.update_data(promo_code=code)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"Код: *{code}*\n\nВведите *сумму* (в рублях):",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_promo_amount)


@router.message(Form.admin_promo_amount)
async def admin_promo_amount_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите положительное число:")
        return
    await state.update_data(promo_amount=amount)
    admin_user = get_user(message.from_user.id)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                "Введите *количество использований* (например: 1 или 100):",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.set_state(Form.admin_promo_uses)


@router.message(Form.admin_promo_uses)
async def admin_promo_uses_handler(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        uses = int(message.text.strip())
        if uses <= 0:
            raise ValueError
    except ValueError:
        await bot.send_message(message.chat.id, "Введите положительное число:")
        return

    data       = await state.get_data()
    code       = data["promo_code"]
    amount     = data["promo_amount"]
    admin_user = get_user(message.from_user.id)
    create_promo(code, amount, uses)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"✅ Промокод создан!\n\n"
                f"🎫 Код: *{code}*\n"
                f"💰 Сумма: *{amount}₽*\n"
                f"🔢 Использований: *{uses}*",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


# ───────────────────────── ЗАПУСК ─────────────────────────

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(token=API_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    asyncio.create_task(check_subscriptions(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
