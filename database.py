import os
from datetime import datetime, timezone
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


# =========================================================
# DATABASE CONNECTION
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL belum tersedia di environment."
    )


@contextmanager
def get_db():

    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    )

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# INIT DATABASE
# =========================================================

def init_database():

    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                balance BIGINT NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                balance_before BIGINT NOT NULL,
                balance_after BIGINT NOT NULL,
                transaction_type TEXT NOT NULL,
                reference TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id BIGSERIAL PRIMARY KEY,
                deposit_id TEXT UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                payment_reference TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                country TEXT,
                service TEXT,
                sell_price BIGINT NOT NULL,
                provider_cost BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                provider_order_id TEXT,
                refund_status TEXT NOT NULL DEFAULT 'NONE',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)


# =========================================================
# TIME
# =========================================================

def now():

    return datetime.now(
        timezone.utc
    ).isoformat()


# =========================================================
# CREATE USER
# =========================================================

def create_user(
    telegram_id,
    username=None,
    first_name=None,
):

    with get_db() as db:

        existing = db.execute(
            """
            SELECT *
            FROM users
            WHERE telegram_id = %s
            """,
            (telegram_id,)
        ).fetchone()

        if existing:

            db.execute(
                """
                UPDATE users
                SET username = %s,
                    first_name = %s
                WHERE telegram_id = %s
                """,
                (
                    username,
                    first_name,
                    telegram_id,
                )
            )

            return

        db.execute(
            """
            INSERT INTO users
            (
                telegram_id,
                username,
                first_name,
                balance,
                created_at
            )
            VALUES (%s, %s, %s, 0, %s)
            """,
            (
                telegram_id,
                username,
                first_name,
                now(),
            )
        )


# =========================================================
# GET USER
# =========================================================

def get_user(telegram_id):

    with get_db() as db:

        return db.execute(
            """
            SELECT *
            FROM users
            WHERE telegram_id = %s
            """,
            (telegram_id,)
        ).fetchone()


# =========================================================
# GET BALANCE
# =========================================================

def get_balance(telegram_id):

    user = get_user(telegram_id)

    if not user:
        create_user(telegram_id)
        return 0

    return user["balance"]


# =========================================================
# ADD BALANCE
# =========================================================

def add_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None,
):

    if amount <= 0:
        raise ValueError(
            "Amount harus lebih besar dari 0."
        )

    with get_db() as db:

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (telegram_id,)
        ).fetchone()

        if not user:
            raise ValueError(
                "User belum terdaftar."
            )

        before = user["balance"]
        after = before + amount

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id,
            )
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                telegram_id,
                amount,
                before,
                after,
                transaction_type,
                reference,
                description,
                now(),
            )
        )

        return after


# =========================================================
# SUBTRACT BALANCE
# =========================================================

def subtract_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None,
):

    if amount <= 0:
        raise ValueError(
            "Amount harus lebih besar dari 0."
        )

    with get_db() as db:

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (telegram_id,)
        ).fetchone()

        if not user:
            raise ValueError(
                "User belum terdaftar."
            )

        before = user["balance"]

        if before < amount:
            raise ValueError(
                "Saldo tidak cukup."
            )

        after = before - amount

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id,
            )
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                telegram_id,
                -amount,
                before,
                after,
                transaction_type,
                reference,
                description,
                now(),
            )
        )

        return after


# =========================================================
# COMPLETE DEPOSIT
# =========================================================

def complete_deposit(
    deposit_id,
    payment_reference=None,
):
    """
    Menyelesaikan deposit secara atomik.

    Return:
        (
            completed,
            telegram_id,
            amount,
            new_balance
        )

    completed = True
        deposit baru berhasil dikreditkan

    completed = False
        deposit sudah pernah diselesaikan
    """

    with get_db() as db:

        # -------------------------------------------------
        # Ambil deposit dan kunci barisnya
        # -------------------------------------------------

        deposit = db.execute(
            """
            SELECT
                id,
                deposit_id,
                telegram_id,
                amount,
                status
            FROM deposits
            WHERE deposit_id = %s
            FOR UPDATE
            """,
            (deposit_id,)
        ).fetchone()

        if not deposit:
            raise ValueError(
                f"Deposit tidak ditemukan: {deposit_id}"
            )

        telegram_id = deposit["telegram_id"]
        amount = deposit["amount"]
        status = deposit["status"]

        # -------------------------------------------------
        # Jangan kredit dua kali
        # -------------------------------------------------

        if status == "COMPLETED":

            user = db.execute(
                """
                SELECT balance
                FROM users
                WHERE telegram_id = %s
                """,
                (telegram_id,)
            ).fetchone()

            if not user:
                return (
                    False,
                    telegram_id,
                    amount,
                    0,
                )

            return (
                False,
                telegram_id,
                amount,
                user["balance"],
            )

        # -------------------------------------------------
        # Pastikan user tersedia
        # -------------------------------------------------

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (telegram_id,)
        ).fetchone()

        if not user:
            raise ValueError(
                f"User tidak ditemukan: {telegram_id}"
            )

        # -------------------------------------------------
        # Hitung saldo baru
        # -------------------------------------------------

        before = user["balance"]
        after = before + amount

        # -------------------------------------------------
        # Tambahkan saldo user
        # -------------------------------------------------

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id,
            )
        )

        # -------------------------------------------------
        # Catat ke ledger
        # -------------------------------------------------

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                telegram_id,
                amount,
                before,
                after,
                "DEPOSIT",
                deposit_id,
                "Deposit berhasil dikonfirmasi oleh Xendit.",
                now(),
            )
        )

        # -------------------------------------------------
        # Tandai deposit sebagai COMPLETED
        # -------------------------------------------------

        db.execute(
            """
            UPDATE deposits
            SET
                status = 'COMPLETED',
                payment_reference = %s,
                completed_at = %s
            WHERE deposit_id = %s
            """,
            (
                payment_reference,
                now(),
                deposit_id,
            )
        )

        return (
            True,
            telegram_id,
            amount,
            after,
        )
