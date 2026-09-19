import asyncio
import logging
import os
import sqlite3
import re
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
DATABASE_URL  = os.getenv("DATABASE_URL", "").strip()


class _CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=()):
        if DATABASE_URL:
            query = query.replace("?", "%s")
            query = query.replace("INSERT OR IGNORE INTO promo_usage", "INSERT INTO promo_usage")
            if query.lstrip().upper().startswith("ALTER TABLE"):
                query = query.replace(" ADD COLUMN ", " ADD COLUMN IF NOT EXISTS ", 1)
            if query.startswith("INSERT OR REPLACE INTO promocodes"):
                query = ("INSERT INTO promocodes (code, amount, uses_left) VALUES (%s,%s,%s) "
                         "ON CONFLICT (code) DO UPDATE SET amount=EXCLUDED.amount, uses_left=EXCLUDED.uses_left")
            if query.startswith("INSERT INTO promo_usage"):
                query += " ON CONFLICT (user_id, code) DO NOTHING"
        return self._cursor.execute(query, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _CompatConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return _CompatCursor(self._connection.cursor())

    def __getattr__(self, name):
        return getattr(self._connection, name)


def connect_db():
    if DATABASE_URL:
        import psycopg
        return _CompatConnection(psycopg.connect(DATABASE_URL))
    return _CompatConnection(connect_db())

router = Router()


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ Р“Р»РѕР±Р°Р»СЊРЅС‹Р№ РѕР±СЂР°Р±РѕС‚С‡РёРє РѕС€РёР±РѕРє в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РРјСЏ РєР»РёРµРЅС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def make_client_name(user_id: int) -> str:
    return f"u{user_id}"


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ H1VLESS API в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ Р‘Р” в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def init_db():
    con = connect_db()
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
        except Exception:
            pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promocodes (
            code      TEXT PRIMARY KEY,
            amount    INTEGER,
            uses_left INTEGER
        )
    """)

    # РўР°Р±Р»РёС†Р° РёСЃРїРѕР»СЊР·РѕРІР°РЅРЅС‹С… РїСЂРѕРјРѕРєРѕРґРѕРІ (РѕРґРёРЅ СЋР·РµСЂ = РѕРґРёРЅ СЂР°Р·)
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
    con = connect_db()
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
    con = connect_db()
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
    con = connect_db()
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
    con = connect_db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM promo_usage WHERE user_id=? AND code=?", (user_id, code))
    result = cur.fetchone()
    con.close()
    return result is not None


def mark_promo_used(user_id: int, code: str):
    con = connect_db()
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO promo_usage (user_id, code) VALUES (?,?)",
        (user_id, code),
    )
    con.commit()
    con.close()


def set_notified(user_id: int, field: str):
    con = connect_db()
    cur = con.cursor()
    cur.execute(f"UPDATE users SET {field}=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def reset_notifications(user_id: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET notified_3d=0, notified_0d=0 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def ensure_user(user_id: int, username: str = "", referrer_id: int = None) -> bool:
    con = connect_db()
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
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET main_msg_id=? WHERE user_id=?", (msg_id, user_id))
    con.commit()
    con.close()


def set_agreed(user_id: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET agreed=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def set_balance(user_id: int, balance: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (balance, user_id))
    con.commit()
    con.close()


def increment_refs(referrer_id: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (referrer_id,))
    con.commit()
    con.close()


def add_ref_earned(referrer_id: int, amount: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET ref_earned = ref_earned + ?, balance = balance + ? WHERE user_id=?",
        (amount, amount, referrer_id),
    )
    con.commit()
    con.close()


def set_sub(user_id: int, name: str, expire: str, link: str, sub_url: str = None):
    con = connect_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET sub_name=?, sub_expire=?, sub_link=?, sub_url=? WHERE user_id=?",
        (name, expire, link, sub_url, user_id),
    )
    con.commit()
    con.close()


def clear_sub(user_id: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE users SET sub_name=NULL, sub_expire=NULL, sub_link=NULL, sub_url=NULL WHERE user_id=?",
        (user_id,),
    )
    con.commit()
    con.close()


def get_promo(code: str) -> dict | None:
    con = connect_db()
    cur = con.cursor()
    cur.execute("SELECT code, amount, uses_left FROM promocodes WHERE code=?", (code,))
    row = cur.fetchone()
    con.close()
    if row:
        return {"code": row[0], "amount": row[1], "uses_left": row[2]}
    return None


def create_promo(code: str, amount: int, uses: int):
    con = connect_db()
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO promocodes (code, amount, uses_left) VALUES (?,?,?)",
        (code, amount, uses),
    )
    con.commit()
    con.close()


def use_promo(code: str):
    con = connect_db()
    cur = con.cursor()
    cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code=?", (code,))
    cur.execute("DELETE FROM promocodes WHERE uses_left <= 0")
    con.commit()
    con.close()


def find_user_by_username(username: str) -> dict | None:
    clean = username.lstrip("@").strip()
    con   = connect_db()
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


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ FSM в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РҐРµР»РїРµСЂС‹ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def btn(text: str, cbd: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cbd)


def format_key_text(vless_key: str | None, sub_link: str | None) -> str:
    if sub_link:
        return (
            f"рџ”— *РЎСЃС‹Р»РєР° РЅР° РїРѕРґРїРёСЃРєСѓ:*\n"
            f"`{sub_link}`\n\n"
            f"рџ“І РљР°Рє РґРѕР±Р°РІРёС‚СЊ:\n"
            f"вЂў *Hiddify* в†’ + в†’ Р’СЃС‚Р°РІРёС‚СЊ СЃСЃС‹Р»РєСѓ\n"
            f"вЂў *V2RayTun* в†’ + в†’ РРјРїРѕСЂС‚ РїРѕ СЃСЃС‹Р»РєРµ\n"
            f"вЂў *Streisand* в†’ + в†’ Р’СЃС‚Р°РІРёС‚СЊ URL\n"
            f"вЂў *V2Box* в†’ + в†’ РРјРїРѕСЂС‚ РёР· Р±СѓС„РµСЂР°"
        )
    elif vless_key:
        return (
            f"рџ”‘ *Р’Р°С€ РєР»СЋС‡ РїРѕРґРєР»СЋС‡РµРЅРёСЏ:*\n"
            f"`{vless_key}`\n\n"
            f"рџ“І РљР°Рє РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ:\n"
            f"вЂў *Hiddify* в†’ + в†’ Р”РѕР±Р°РІРёС‚СЊ СЃСЃС‹Р»РєСѓ\n"
            f"вЂў *V2RayTun* в†’ + в†’ РРјРїРѕСЂС‚ РёР· Р±СѓС„РµСЂР°\n"
            f"вЂў *Streisand* в†’ + в†’ Р’СЃС‚Р°РІРёС‚СЊ\n"
            f"вЂў *V2Box* в†’ + в†’ Р’СЃС‚Р°РІРёС‚СЊ СЃСЃС‹Р»РєСѓ"
        )
    return "вќЊ РљР»СЋС‡ РЅРµ РїРѕР»СѓС‡РµРЅ. РћР±СЂР°С‚РёС‚РµСЃСЊ РІ РїРѕРґРґРµСЂР¶РєСѓ: @takurwa"


def is_admin(user) -> bool:
    return (user.username or "").lower() == ADMIN_USERNAME.lower()


async def safe_delete(bot: Bot, chat_id: int, msg_id: int):
    """Р‘РµР·РѕРїР°СЃРЅРѕ СѓРґР°Р»СЏРµС‚ СЃРѕРѕР±С‰РµРЅРёРµ"""
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


async def safe_edit(message, text: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Р‘РµР·РѕРїР°СЃРЅРѕ СЂРµРґР°РєС‚РёСЂСѓРµС‚ СЃРѕРѕР±С‰РµРЅРёРµ"""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception:
        return False


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РљР»Р°РІРёР°С‚СѓСЂС‹ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def get_welcome_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="рџ›Ў РџРѕР»РёС‚РёРєР° РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё в†—", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [InlineKeyboardButton(text="рџ“‹ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРµ СЃРѕРіР»Р°С€РµРЅРёРµ в†—", url="https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15")],
        [InlineKeyboardButton(text="рџ’° РЈСЃР»РѕРІРёСЏ РІРѕР·РІСЂР°С‚Р° в†—",            url="https://t.me/takurwa")],
        [btn("вњ… РЇ РѕР·РЅР°РєРѕРјР»РµРЅ Рё РїСЂРёРЅРёРјР°СЋ СѓСЃР»РѕРІРёСЏ", "agree")],
    ])


def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("рџ›’ РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")],
        [btn("рџ“Љ РњРѕСЏ РїРѕРґРїРёСЃРєР°",    "my_sub")],
        [btn("рџ’° Р‘Р°Р»Р°РЅСЃ", "balance"), btn("рџЋ« РџСЂРѕРјРѕРєРѕРґ", "promocode")],
        [btn("рџ‘Ґ РџСЂРёРіР»Р°СЃРёС‚СЊ РґСЂСѓР·РµР№", "ref")],
        [InlineKeyboardButton(text="рџ† РџРѕРґРґРµСЂР¶РєР° в†—", url="https://t.me/takurwa")],
        [btn("рџ“„ Р”РѕРєСѓРјРµРЅС‚С‹", "docs")],
    ])


def get_devices_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("рџ“± 3 СѓСЃС‚СЂРѕР№СЃС‚РІР°",  "devices_3")],
        [btn("рџ“±рџ“± 6 СѓСЃС‚СЂРѕР№СЃС‚РІ", "devices_6")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ",        "main_menu")],
    ])


def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("рџ’ё Р’С‹РґР°С‚СЊ РґРµРЅСЊРіРё",      "admin_give")],
        [btn("рџЋ« РЎРѕР·РґР°С‚СЊ РїСЂРѕРјРѕРєРѕРґ",    "admin_create_promo")],
        [btn("вњ… Р’С‹РґР°С‚СЊ РїРѕРґРїРёСЃРєСѓ",     "admin_give_sub")],
        [btn("вќЊ РЈРґР°Р»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ",    "admin_del_sub")],
        [btn("рџ“± РР·РјРµРЅРёС‚СЊ СѓСЃС‚СЂРѕР№СЃС‚РІР°", "admin_change_dev")],
        [btn("рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°",         "admin_stats")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",       "main_menu")],
    ])


def get_docs_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="рџ›Ў РџРѕР»РёС‚РёРєР° РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё в†—", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [InlineKeyboardButton(text="рџ“‹ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРµ СЃРѕРіР»Р°С€РµРЅРёРµ в†—", url="https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15")],
        [InlineKeyboardButton(text="рџ’° РЈСЃР»РѕРІРёСЏ РІРѕР·РІСЂР°С‚Р° в†—",            url="https://t.me/takurwa")],
        [btn("рџ”’ РџРѕР»РёС‚РёРєР° Р±РѕС‚Р°", "bot_policy")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",  "main_menu")],
    ])


TARIFFS = {
    "3_1d": ("1 РґРµРЅСЊ (РїСЂРѕР±РЅС‹Р№)",     29,   1, 3),
    "3_1m": ("1 РјРµСЃСЏС†",              99,  30, 3),
    "3_3m": ("3 РјРµСЃСЏС†Р°",            219,  90, 3),
    "3_6m": ("6 РјРµСЃСЏС†РµРІ",           449, 180, 3),
    "3_1y": ("1 РіРѕРґ",               739, 365, 3),
    "6_1m": ("1 РјРµСЃСЏС† (6 СѓСЃС‚СЂ.)",   119,  30, 6),
    "6_3m": ("3 РјРµСЃСЏС†Р° (6 СѓСЃС‚СЂ.)",  239,  90, 6),
    "6_6m": ("6 РјРµСЃСЏС†РµРІ (6 СѓСЃС‚СЂ.)", 479, 180, 6),
    "6_1y": ("1 РіРѕРґ (6 СѓСЃС‚СЂ.)",     849, 365, 6),
}

REF_BONUS_PERCENT = 20

MAIN_TEXT = (
    "рџ’Ћ *FLEX VPN*\n\n"
    "рџЊђ РћС‚ 5 СЃРµСЂРІРµСЂРѕРІ РІ СЂР°Р·РЅС‹С… СЃС‚СЂР°РЅР°С…\n"
    "рџ›Ў Р‘РµР· Р»РѕРіРѕРІ РїРѕРґРєР»СЋС‡РµРЅРёР№\n"
    "рџ”’ РќР°РґС‘Р¶РЅРѕРµ РїРѕРґРєР»СЋС‡РµРЅРёРµ\n"
    "рџљЂ Р’С‹СЃРѕРєР°СЏ СЃРєРѕСЂРѕСЃС‚СЊ СЃРѕРµРґРёРЅРµРЅРёСЏ"
)


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РџСЂРѕРІРµСЂРєР° РїРѕРґРїРёСЃРѕРє в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
                            [btn("рџ”„ РџСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"вљ пёЏ *Р’Р°С€Р° РїРѕРґРїРёСЃРєР° РёСЃС‚РµРєР°РµС‚ С‡РµСЂРµР· 3 РґРЅСЏ!*\n\n"
                            f"рџ“… Р”Р°С‚Р° РѕРєРѕРЅС‡Р°РЅРёСЏ: *{expire.strftime('%d.%m.%Y')}*\n\n"
                            f"РџСЂРѕРґР»РёС‚Рµ РїРѕРґРїРёСЃРєСѓ С‡С‚РѕР±С‹ РЅРµ РїРѕС‚РµСЂСЏС‚СЊ РґРѕСЃС‚СѓРї.",
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
                            [btn("рџ”„ РџСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"рџ”ґ *Р’Р°С€Р° РїРѕРґРїРёСЃРєР° РёСЃС‚РµРєР°РµС‚ СЃРµРіРѕРґРЅСЏ!*\n\n"
                            f"РџСЂРѕРґР»РёС‚Рµ РїСЂСЏРјРѕ СЃРµР№С‡Р°СЃ С‡С‚РѕР±С‹ РЅРµ РїРѕС‚РµСЂСЏС‚СЊ РґРѕСЃС‚СѓРї.",
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
                            [btn("рџ›’ РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")]
                        ])
                        await bot.send_message(
                            user_id,
                            f"вќЊ *Р’Р°С€Р° РїРѕРґРїРёСЃРєР° РёСЃС‚РµРєР»Р°!*\n\n"
                            f"Р”РѕСЃС‚СѓРї Рє VPN РѕС‚РєР»СЋС‡С‘РЅ.\n"
                            f"РћС„РѕСЂРјРёС‚Рµ РЅРѕРІСѓСЋ РїРѕРґРїРёСЃРєСѓ С‡С‚РѕР±С‹ РїСЂРѕРґРѕР»Р¶РёС‚СЊ.",
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


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РҐРµРЅРґР»РµСЂС‹ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
                "рџ‘‹ *Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ РІ FLEX VPN!*\n\n"
                "РџРµСЂРµРґ РЅР°С‡Р°Р»РѕРј СЂР°Р±РѕС‚С‹ РѕР·РЅР°РєРѕРјСЊС‚РµСЃСЊ СЃ РґРѕРєСѓРјРµРЅС‚Р°РјРё СЃРµСЂРІРёСЃР°.\n\n"
                "рџ“„ РџСЂРѕС‡РёС‚Р°Р№С‚Рµ:\n"
                "вЂў РџРѕР»РёС‚РёРєСѓ РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё\n"
                "вЂў РџРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРµ СЃРѕРіР»Р°С€РµРЅРёРµ\n"
                "вЂў РЈСЃР»РѕРІРёСЏ РІРѕР·РІСЂР°С‚Р°\n\n"
                "РџРѕСЃР»Рµ РѕР·РЅР°РєРѕРјР»РµРЅРёСЏ РЅР°Р¶РјРёС‚Рµ РєРЅРѕРїРєСѓ РЅРёР¶Рµ рџ‘‡",
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
            "рџ‘‘ *РђРґРјРёРЅ-РїР°РЅРµР»СЊ FLEX VPN*",
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
        f"рџ“Љ *РЎС‚Р°С‚РёСЃС‚РёРєР° FLEX VPN*\n\n"
        f"рџ‘¤ Р’СЃРµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№: *{stats['total_users']}*\n"
        f"вњ… РђРєС‚РёРІРЅС‹С… РїРѕРґРїРёСЃРѕРє: *{stats['active_subs']}*\n"
        f"рџ’° Р’С‹РїР»Р°С‡РµРЅРѕ СЂРµС„РµСЂР°Р»Р°Рј: *{stats['total_earned']}в‚Ѕ*"
    )

    # РЈРґР°Р»СЏРµРј СЃС‚Р°СЂРѕРµ СЃРѕРѕР±С‰РµРЅРёРµ Рё РѕС‚РїСЂР°РІР»СЏРµРј РЅРѕРІРѕРµ
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
        await callback.answer("в›” РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    stats = get_stats()
    text  = (
        f"рџ“Љ *РЎС‚Р°С‚РёСЃС‚РёРєР° FLEX VPN*\n\n"
        f"рџ‘¤ Р’СЃРµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№: *{stats['total_users']}*\n"
        f"вњ… РђРєС‚РёРІРЅС‹С… РїРѕРґРїРёСЃРѕРє: *{stats['active_subs']}*\n"
        f"рџ’° Р’С‹РїР»Р°С‡РµРЅРѕ СЂРµС„РµСЂР°Р»Р°Рј: *{stats['total_earned']}в‚Ѕ*"
    )
    await safe_edit(callback.message, text, reply_markup=get_admin_menu())
    await callback.answer()


@router.callback_query(F.data == "agree")
async def agree_cb(callback: CallbackQuery):
    ensure_user(callback.from_user.id, callback.from_user.username or "")
    set_agreed(callback.from_user.id)
    await safe_edit(callback.message, MAIN_TEXT, reply_markup=get_main_menu())
    await callback.answer("вњ… Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ!")


@router.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery):
    await safe_edit(callback.message, MAIN_TEXT, reply_markup=get_main_menu())
    await callback.answer()


@router.callback_query(F.data == "buy_sub")
async def buy_sub_cb(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "рџ“± *Р’С‹Р±РµСЂРёС‚Рµ РєРѕР»РёС‡РµСЃС‚РІРѕ СѓСЃС‚СЂРѕР№СЃС‚РІ*\n\n"
        "РџРѕРґРїРёСЃРєР° Р±СѓРґРµС‚ СЂР°Р±РѕС‚Р°С‚СЊ РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ РЅР° РІС‹Р±СЂР°РЅРЅРѕРј С‡РёСЃР»Рµ СѓСЃС‚СЂРѕР№СЃС‚РІ.",
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
            f"рџ“… *Р’С‹Р±РµСЂРёС‚Рµ РїРµСЂРёРѕРґ РїРѕРґРїРёСЃРєРё*\n\n"
            f"рџ’° Р’Р°С€ Р±Р°Р»Р°РЅСЃ: *{balance}в‚Ѕ*\n"
            f"рџ“± РЈСЃС‚СЂРѕР№СЃС‚РІ: *3*\n"
            f"рџ“Љ РўСЂР°С„РёРє: *150 Р“Р‘/РјРµСЃ*"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("рџџў 1 РґРµРЅСЊ (РїСЂРѕР±РЅС‹Р№) вЂ” 29в‚Ѕ", "tar_3_1d")],
            [btn("рџ”µ 1 РјРµСЃСЏС† вЂ” 99в‚Ѕ",           "tar_3_1m")],
            [btn("рџџЈ 3 РјРµСЃСЏС†Р° вЂ” 219в‚Ѕ",         "tar_3_3m")],
            [btn("рџџ  6 РјРµСЃСЏС†РµРІ вЂ” 449в‚Ѕ",        "tar_3_6m")],
            [btn("рџЏ† 1 РіРѕРґ вЂ” 739в‚Ѕ",            "tar_3_1y")],
            [btn("в—ЂпёЏ РќР°Р·Р°Рґ",                   "buy_sub")],
        ])
    else:
        text = (
            f"рџ“… *Р’С‹Р±РµСЂРёС‚Рµ РїРµСЂРёРѕРґ РїРѕРґРїРёСЃРєРё*\n\n"
            f"рџ’° Р’Р°С€ Р±Р°Р»Р°РЅСЃ: *{balance}в‚Ѕ*\n"
            f"рџ“± РЈСЃС‚СЂРѕР№СЃС‚РІ: *6*\n"
            f"рџ“Љ РўСЂР°С„РёРє: *300 Р“Р‘/РјРµСЃ*"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("рџ”µ 1 РјРµСЃСЏС† вЂ” 119в‚Ѕ",   "tar_6_1m")],
            [btn("рџџЈ 3 РјРµСЃСЏС†Р° вЂ” 239в‚Ѕ",  "tar_6_3m")],
            [btn("рџџ  6 РјРµСЃСЏС†РµРІ вЂ” 479в‚Ѕ", "tar_6_6m")],
            [btn("рџЏ† 1 РіРѕРґ вЂ” 849в‚Ѕ",     "tar_6_1y")],
            [btn("в—ЂпёЏ РќР°Р·Р°Рґ",            "buy_sub")],
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
        await callback.answer("вќЊ РќРµРёР·РІРµСЃС‚РЅС‹Р№ С‚Р°СЂРёС„.", show_alert=True)
        return

    name, price, days, devices = TARIFFS[tariff_code]
    user    = get_user(user_id)
    balance = user["balance"]

    if balance < price:
        await callback.answer(
            f"вќЊ РќРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ СЃСЂРµРґСЃС‚РІ!\nРќСѓР¶РЅРѕ {price}в‚Ѕ, Сѓ РІР°СЃ {balance}в‚Ѕ.",
            show_alert=True,
        )
        return

    await callback.answer("вЏі РЎРѕР·РґР°С‘Рј РїРѕРґРїРёСЃРєСѓ...")
    await safe_edit(
        callback.message,
        "вЏі *РЎРѕР·РґР°С‘Рј РІР°С€Сѓ РїРѕРґРїРёСЃРєСѓ...*\n\nРџРѕР¶Р°Р»СѓР№СЃС‚Р° РїРѕРґРѕР¶РґРёС‚Рµ.",
    )

    vless_key, sub_link = await h1_get_or_create_client(user_id, days, devices)

    if not vless_key and not sub_link:
        await safe_edit(
            callback.message,
            "вќЊ РћС€РёР±РєР° РїСЂРё СЃРѕР·РґР°РЅРёРё РїРѕРґРїРёСЃРєРё.\nРћР±СЂР°С‚РёС‚РµСЃСЊ РІ РїРѕРґРґРµСЂР¶РєСѓ: @takurwa",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ", "main_menu")]]),
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
                    f"рџЋ‰ РџРѕ РІР°С€РµР№ СЂРµС„РµСЂР°Р»СЊРЅРѕР№ СЃСЃС‹Р»РєРµ СЃРѕРІРµСЂС€РµРЅР° РїРѕРєСѓРїРєР°!\n"
                    f"рџ’° Р’Р°Рј РЅР°С‡РёСЃР»РµРЅРѕ *{bonus}в‚Ѕ* ({REF_BONUS_PERCENT}% РѕС‚ {price}в‚Ѕ)",
                    parse_mode="Markdown",
                )
            except TelegramForbiddenError:
                pass
            except Exception as e:
                logging.error(f"Ref bonus notify error: {e}")

    key_text = format_key_text(vless_key, sub_link)
    title    = "рџ”„ *РџРѕРґРїРёСЃРєР° РїСЂРѕРґР»РµРЅР°!*" if is_renewal else "вњ… *РџРѕРґРїРёСЃРєР° Р°РєС‚РёРІРёСЂРѕРІР°РЅР°!*"

    text = (
        f"{title}\n\n"
        f"рџ“… {name}\n"
        f"рџ“± РЈСЃС‚СЂРѕР№СЃС‚РІ: *{devices}*\n"
        f"вЏі Р”РµР№СЃС‚РІСѓРµС‚ РґРѕ: *{expire_date.strftime('%d.%m.%Y')}*\n"
        f"рџ’° РЎРїРёСЃР°РЅРѕ: *{price}в‚Ѕ*\n"
        f"рџ’і РћСЃС‚Р°С‚РѕРє: *{new_balance}в‚Ѕ*\n\n"
        f"{key_text}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("рџ“‹ РЎРєРѕРїРёСЂРѕРІР°С‚СЊ РєР»СЋС‡", "copy_key")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",    "main_menu")],
    ])
    await safe_edit(callback.message, text, reply_markup=kb)


@router.callback_query(F.data == "my_sub")
async def my_sub_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user = get_user(user_id)

    if not user["sub_name"]:
        text = (
            "рџ“Љ *РњРѕСЏ РїРѕРґРїРёСЃРєР°*\n\n"
            "РЈ РІР°СЃ РїРѕРєР° РЅРµС‚ Р°РєС‚РёРІРЅРѕР№ РїРѕРґРїРёСЃРєРё.\n"
            "РћС„РѕСЂРјРёС‚Рµ РµС‘ РІ СЂР°Р·РґРµР»Рµ В«РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓВ»."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("рџ›’ РљСѓРїРёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")],
            [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",   "main_menu")],
        ])
    else:
        try:
            expire    = datetime.strptime(user["sub_expire"], "%d.%m.%Y %H:%M")
            days_left = (expire - datetime.now()).days
            days_text = f"вЏі РћСЃС‚Р°Р»РѕСЃСЊ: *{days_left} РґРЅ.*\n" if days_left >= 0 else "рџ”ґ РџРѕРґРїРёСЃРєР° РёСЃС‚РµРєР»Р°\n"
        except Exception:
            days_text = ""

        key_text = format_key_text(user.get("sub_link"), user.get("sub_url"))
        text = (
            f"рџ“Љ *РњРѕСЏ РїРѕРґРїРёСЃРєР°*\n\n"
            f"рџ“… РўР°СЂРёС„: *{user['sub_name']}*\n"
            f"рџ“† Р”Рѕ: *{user['sub_expire']}*\n"
            f"{days_text}\n"
            f"{key_text}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("рџ“‹ РЎРєРѕРїРёСЂРѕРІР°С‚СЊ РєР»СЋС‡",  "copy_key")],
            [btn("рџ”„ РџСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ", "buy_sub")],
            [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",     "main_menu")],
        ])

    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "copy_key")
async def copy_key_cb(callback: CallbackQuery):
    await callback.answer("РљР»СЋС‡ СЃРєРѕРїРёСЂРѕРІР°РЅ РІ Р±СѓС„РµСЂ РѕР±РјРµРЅР°!", show_alert=True)


@router.callback_query(F.data == "balance")
async def balance_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id, callback.from_user.username or "")
    user = get_user(user_id)
    kb   = InlineKeyboardMarkup(inline_keyboard=[
        [btn("рџ’і РџРѕРїРѕР»РЅРёС‚СЊ Р±Р°Р»Р°РЅСЃ", "topup")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ",    "main_menu")],
    ])
    await safe_edit(callback.message, f"рџ’° Р’Р°С€ Р±Р°Р»Р°РЅСЃ: *{user['balance']}в‚Ѕ*", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "topup")
async def topup_cb(callback: CallbackQuery, state: FSMContext):
    await safe_edit(
        callback.message,
        "рџ’і *Р’РІРµРґРёС‚Рµ СЃСѓРјРјСѓ РїРѕРїРѕР»РЅРµРЅРёСЏ РІ СЂСѓР±Р»СЏС… (РЅР°РїСЂРёРјРµСЂ: 100):*\n\nРР»Рё РЅР°Р¶РјРёС‚Рµ /start С‡С‚РѕР±С‹ РѕС‚РјРµРЅРёС‚СЊ.",
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
        await bot.send_message(user_id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ:")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="рџ’і РћРїР»Р°С‚РёС‚СЊ в†—", url="https://t.me/durov")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ", "main_menu")],
    ])
    user = get_user(user_id)
    text = f"рџ’і *РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР° РЅР° {amount}в‚Ѕ*\n\nРќР°Р¶РјРёС‚Рµ В«РћРїР»Р°С‚РёС‚СЊВ» вЂ” Р±Р°Р»Р°РЅСЃ РїРѕРїРѕР»РЅРёС‚СЃСЏ РїРѕСЃР»Рµ РѕРїР»Р°С‚С‹."

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
        "рџЋ« *Р’РІРµРґРёС‚Рµ РїСЂРѕРјРѕРєРѕРґ СЃРѕРѕР±С‰РµРЅРёРµРј:*\n\nРР»Рё РЅР°Р¶РјРёС‚Рµ /start С‡С‚РѕР±С‹ РѕС‚РјРµРЅРёС‚СЊ.",
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
        result_text = "вќЊ Р’С‹ СѓР¶Рµ РёСЃРїРѕР»СЊР·РѕРІР°Р»Рё СЌС‚РѕС‚ РїСЂРѕРјРѕРєРѕРґ."
    else:
        promo = get_promo(code)
        if not promo:
            result_text = "вќЊ РџСЂРѕРјРѕРєРѕРґ РЅРµ РЅР°Р№РґРµРЅ РёР»Рё СѓР¶Рµ РёСЃРїРѕР»СЊР·РѕРІР°РЅ."
        else:
            ensure_user(user_id, message.from_user.username or "")
            set_balance(user_id, user["balance"] + promo["amount"])
            use_promo(code)
            mark_promo_used(user_id, code)
            result_text = f"вњ… РџСЂРѕРјРѕРєРѕРґ Р°РєС‚РёРІРёСЂРѕРІР°РЅ!\nрџ’° Р—Р°С‡РёСЃР»РµРЅРѕ *{promo['amount']}в‚Ѕ*"

    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ", "main_menu")]])

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
        f"рџ‘Ґ *Р—Р°СЂР°Р±РѕС‚Р°С‚СЊ СЃ FLEX VPN*\n\n"
        f"РџСЂРёРіР»Р°С€Р°Р№ РґСЂСѓР·РµР№ Рё РїРѕР»СѓС‡Р°Р№ *{REF_BONUS_PERCENT}%* СЃ РёС… РѕРїР»Р°С‚ РЅР° Р±Р°Р»Р°РЅСЃ.\n\n"
        f"рџ‘¤ Р”СЂСѓР·РµР№ РїСЂРёРіР»Р°С€РµРЅРѕ: *{user['refs']}*\n"
        f"рџЋЇ РђРєС‚РёРІРёСЂРѕРІР°Р»Рё РїСЂРѕР±РЅС‹Р№: *{user['ref_trials']}*\n"
        f"рџ’µ Р—Р°СЂР°Р±РѕС‚Р°РЅРѕ: *{user['ref_earned']}в‚Ѕ*\n\n"
        f"рџ”— *Р’Р°С€Р° СЂРµС„РµСЂР°Р»СЊРЅР°СЏ СЃСЃС‹Р»РєР°:*\n`{ref_link}`"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("в—ЂпёЏ РќР°Р·Р°Рґ РІ РјРµРЅСЋ", "main_menu")]])
    await safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "docs")
async def docs_cb(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "рџ“„ *Р”РѕРєСѓРјРµРЅС‚С‹ FLEX VPN*\n\nР’СЃСЏ РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ СЃРµСЂРІРёСЃР° РІСЃРµРіРґР° РІ РѕС‚РєСЂС‹С‚РѕРј РґРѕСЃС‚СѓРїРµ.\n\nрџ“Њ РЎР»СѓР¶Р±Р° РїРѕРґРґРµСЂР¶РєРё: @takurwa",
        reply_markup=get_docs_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "bot_policy")
async def bot_policy_cb(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="рџ›Ў РџРѕР»РёС‚РёРєР° РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё в†—", url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99")],
        [btn("в—ЂпёЏ РќР°Р·Р°Рґ", "docs")],
    ])
    await safe_edit(
        callback.message,
        "рџ”’ *РџРѕР»РёС‚РёРєР° Р±РѕС‚Р° FLEX VPN*\n\n"
        "вЂў Р‘РѕС‚ СЃРѕР±РёСЂР°РµС‚ С‚РѕР»СЊРєРѕ РЅРµРѕР±С…РѕРґРёРјС‹Рµ РґР°РЅРЅС‹Рµ\n"
        "вЂў Р”Р°РЅРЅС‹Рµ РЅРµ РїРµСЂРµРґР°СЋС‚СЃСЏ С‚СЂРµС‚СЊРёРј Р»РёС†Р°Рј\n"
        "вЂў РСЃРїРѕР»СЊР·СѓСЋС‚СЃСЏ РёСЃРєР»СЋС‡РёС‚РµР»СЊРЅРѕ РґР»СЏ РїСЂРµРґРѕСЃС‚Р°РІР»РµРЅРёСЏ СѓСЃР»СѓРі VPN\n"
        "вЂў Р—Р°РїСЂРѕСЃ РЅР° СѓРґР°Р»РµРЅРёРµ РґР°РЅРЅС‹С…: @takurwa",
        reply_markup=kb,
    )
    await callback.answer()


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ РђР”РњРРќ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

async def admin_ask(callback: CallbackQuery, state: FSMContext, text: str, next_state):
    if not is_admin(callback.from_user):
        await callback.answer("в›” РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return
    await safe_edit(callback.message, text)
    await state.set_state(next_state)
    await callback.answer()


@router.callback_query(F.data == "admin_give")
async def admin_give_cb(callback: CallbackQuery, state: FSMContext):
    await admin_ask(callback, state, "рџ’ё Р’РІРµРґРёС‚Рµ *@username* РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:", Form.admin_give_username)


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
                    f"вќЊ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ @{username} РЅРµ РЅР°Р№РґРµРЅ.\n\nР’РІРµРґРёС‚Рµ РґСЂСѓРіРѕР№ username:",
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
                f"вњ… РќР°Р№РґРµРЅ: @{username}\nрџ’° Р‘Р°Р»Р°РЅСЃ: *{get_user(user['user_id'])['balance']}в‚Ѕ*\n\nР’РІРµРґРёС‚Рµ СЃСѓРјРјСѓ (РѕС‚СЂРёС†Р°С‚РµР»СЊРЅР°СЏ вЂ” СЃРЅСЏС‚РёРµ):",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ С‡РёСЃР»Рѕ (РЅРµ РЅРѕР»СЊ):")
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
                f"вњ… @{target_un}: {sign}{amount}в‚Ѕ\nрџ’° РќРѕРІС‹Р№ Р±Р°Р»Р°РЅСЃ: *{new_bal}в‚Ѕ*",
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
    await admin_ask(callback, state, "вњ… Р’РІРµРґРёС‚Рµ *@username* РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РєРѕС‚РѕСЂРѕРјСѓ РІС‹РґР°С‚СЊ РїРѕРґРїРёСЃРєСѓ:", Form.admin_sub_username)


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
                    f"вќЊ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ @{username} РЅРµ РЅР°Р№РґРµРЅ.\n\nР’РІРµРґРёС‚Рµ РґСЂСѓРіРѕР№ username:",
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
                f"вњ… РќР°Р№РґРµРЅ: @{username}\n\nР’РІРµРґРёС‚Рµ РєРѕР»РёС‡РµСЃС‚РІРѕ *РґРЅРµР№* РїРѕРґРїРёСЃРєРё:",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ РґРЅРµР№:")
        return
    await state.update_data(sub_days=days)
    admin_user = get_user(message.from_user.id)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"Р”РЅРµР№: *{days}*\n\nР’РІРµРґРёС‚Рµ РєРѕР»РёС‡РµСЃС‚РІРѕ *СѓСЃС‚СЂРѕР№СЃС‚РІ*:",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ:")
        return

    data       = await state.get_data()
    target_id  = data["target_id"]
    target_un  = data["target_username"]
    days       = data["sub_days"]
    admin_user = get_user(message.from_user.id)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                "вЏі РЎРѕР·РґР°С‘Рј РїРѕРґРїРёСЃРєСѓ...",
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
                    "вќЊ РћС€РёР±РєР° РїСЂРё СЃРѕР·РґР°РЅРёРё РїРѕРґРїРёСЃРєРё РІ H1VLESS.",
                    chat_id=message.chat.id,
                    message_id=admin_user["main_msg_id"],
                    reply_markup=get_admin_menu(),
                )
            except Exception:
                pass
        await state.clear()
        return

    expire_date = calc_expire_date(target_id, days)
    set_sub(target_id, f"РђРґРјРёРЅ ({days}Рґ, {devices}СѓСЃС‚.)", expire_date.strftime("%d.%m.%Y %H:%M"), vless_key or "", sub_link)
    reset_notifications(target_id)
    key_text = format_key_text(vless_key, sub_link)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"вњ… РџРѕРґРїРёСЃРєР° РІС‹РґР°РЅР° @{target_un}!\n\n"
                f"рџ“… Р”РЅРµР№: *{days}*\n"
                f"рџ“± РЈСЃС‚СЂРѕР№СЃС‚РІ: *{devices}*\n"
                f"вЏі Р”Рѕ: *{expire_date.strftime('%d.%m.%Y')}*\n\n"
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
    await admin_ask(callback, state, "вќЊ Р’РІРµРґРёС‚Рµ *@username* РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Сѓ РєРѕС‚РѕСЂРѕРіРѕ СѓРґР°Р»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ:", Form.admin_del_username)


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
                    f"вќЊ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ @{username} РЅРµ РЅР°Р№РґРµРЅ.",
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
    status    = "вњ… РЈРґР°Р»РµРЅРѕ" if deleted else "вљ пёЏ РљР»РёРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ РІ H1VLESS (Р‘Р” РѕС‡РёС‰РµРЅР°)"

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"{status}\nрџ—‘ РџРѕРґРїРёСЃРєР° @{username} СѓРґР°Р»РµРЅР°.",
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
    await admin_ask(callback, state, "рџ“± Р’РІРµРґРёС‚Рµ *@username* РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:", Form.admin_dev_username)


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
                    f"вќЊ РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ @{username} РЅРµ РЅР°Р№РґРµРЅ.\n\nР’РІРµРґРёС‚Рµ РґСЂСѓРіРѕР№ username:",
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
                f"вњ… РќР°Р№РґРµРЅ: @{username}\n\nР’РІРµРґРёС‚Рµ РЅРѕРІРѕРµ РєРѕР»РёС‡РµСЃС‚РІРѕ СѓСЃС‚СЂРѕР№СЃС‚РІ:",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ:")
        return

    data       = await state.get_data()
    target_id  = data["target_id"]
    target_un  = data["target_username"]
    admin_user = get_user(message.from_user.id)
    success    = await h1_update_devices(target_id, devices)

    result = (
        f"вњ… РЈ @{target_un} СѓСЃС‚Р°РЅРѕРІР»РµРЅ Р»РёРјРёС‚ *{devices}* СѓСЃС‚СЂРѕР№СЃС‚РІ."
        if success else
        f"вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ РёР·РјРµРЅРёС‚СЊ вЂ” РєР»РёРµРЅС‚ РЅРµ РЅР°Р№РґРµРЅ РІ H1VLESS."
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
    await admin_ask(callback, state, "рџЋ« Р’РІРµРґРёС‚Рµ *РєРѕРґ* РїСЂРѕРјРѕРєРѕРґР° (РЅР°РїСЂРёРјРµСЂ: FLEX2024):", Form.admin_promo_code)


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
                f"РљРѕРґ: *{code}*\n\nР’РІРµРґРёС‚Рµ *СЃСѓРјРјСѓ* (РІ СЂСѓР±Р»СЏС…):",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ:")
        return
    await state.update_data(promo_amount=amount)
    admin_user = get_user(message.from_user.id)
    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                "Р’РІРµРґРёС‚Рµ *РєРѕР»РёС‡РµСЃС‚РІРѕ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёР№* (РЅР°РїСЂРёРјРµСЂ: 1 РёР»Рё 100):",
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
        await bot.send_message(message.chat.id, "Р’РІРµРґРёС‚Рµ РїРѕР»РѕР¶РёС‚РµР»СЊРЅРѕРµ С‡РёСЃР»Рѕ:")
        return

    data       = await state.get_data()
    code       = data["promo_code"]
    amount     = data["promo_amount"]
    admin_user = get_user(message.from_user.id)
    create_promo(code, amount, uses)

    if admin_user and admin_user["main_msg_id"]:
        try:
            await bot.edit_message_text(
                f"вњ… РџСЂРѕРјРѕРєРѕРґ СЃРѕР·РґР°РЅ!\n\n"
                f"рџЋ« РљРѕРґ: *{code}*\n"
                f"рџ’° РЎСѓРјРјР°: *{amount}в‚Ѕ*\n"
                f"рџ”ў РСЃРїРѕР»СЊР·РѕРІР°РЅРёР№: *{uses}*",
                chat_id=message.chat.id,
                message_id=admin_user["main_msg_id"],
                reply_markup=get_admin_menu(),
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await state.clear()


# в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ Р—РђРџРЈРЎРљ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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

