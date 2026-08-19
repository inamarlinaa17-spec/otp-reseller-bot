import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager


DATABASE_FILE = "bot.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                balance_before INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                reference TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deposit_id TEXT UNIQUE NOT NULL,
                telegram_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                payment_reference TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                telegram_id INTEGER NOT NULL,
                country TEXT,
                service TEXT,
                sell_price INTEGER NOT NULL,
                provider_cost INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                provider_order_id TEXT,
                refund_status TEXT NOT NULL DEFAULT 'NONE',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)


def now():
    return datetime.now(timezone.utc).isoformat()


def create_user(telegram_id, username=None, first_name=None):
    with get_db() as db:
        existing = db.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        if existing:
            db.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?
                WHERE telegram_id = ?
                """,
                (username, first_name, telegram_id)
            )
            return

        db.execute(
            """
            INSERT INTO users
            (telegram_id, username, first_name, balance, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                telegram_id,
                username,
                first_name,
                now()
            )
        )


def get_user(telegram_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()


def get_balance(telegram_id):
    user = get_user(telegram_id)

    if not user:
        create_user(telegram_id)
        return 0

    return user["balance"]


def add_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None
):
    if amount <= 0:
        raise ValueError("Amount harus lebih besar dari 0.")

    with get_db() as db:

        user = db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        if not user:
            raise ValueError("User belum terdaftar.")

        before = user["balance"]
        after = before + amount

        db.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE telegram_id = ?
            """,
            (after, telegram_id)
        )

        db.execute(
            """
            INSERT INTO ledger
            (
                telegram_id,
                amount,
                balance_before,
                balance_after,
                transaction_type,
                reference,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                amount,
                before,
                after,
                transaction_type,
                reference,
                description,
                now()
            )
        )

        return after


def subtract_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None
):
    if amount <= 0:
        raise ValueError("Amount harus lebih besar dari 0.")

    with get_db() as db:

        user = db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        if not user:
            raise ValueError("User belum terdaftar.")

        before = user["balance"]

        if before < amount:
            raise ValueError("Saldo tidak cukup.")

        after = before - amount

        db.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE telegram_id = ?
            """,
            (after, telegram_id)
        )

        db.execute(
            """
            INSERT INTO ledger
            (
                telegram_id,
                amount,
                balance_before,
                balance_after,
                transaction_type,
                reference,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                -amount,
                before,
                after,
                transaction_type,
                reference,
                description,
                now()
            )
        )

        return after
