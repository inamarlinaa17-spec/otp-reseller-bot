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
                last_checkin_at TEXT,
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
            CREATE TABLE IF NOT EXISTS otp_quotes (
                quote_id TEXT PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                provider TEXT NOT NULL,
                country TEXT NOT NULL,
                country_name TEXT,
                service TEXT NOT NULL,
                operator TEXT,
                pool TEXT,
                cost_usd DOUBLE PRECISION NOT NULL,
                stock BIGINT NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            ALTER TABLE otp_quotes ADD COLUMN IF NOT EXISTS country_name TEXT
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                country TEXT,
                country_name TEXT,
                service TEXT,
                service_name TEXT,
                provider TEXT NOT NULL DEFAULT '5sim',
                operator TEXT,
                phone TEXT,
                expires_at TEXT,
                sell_price BIGINT NOT NULL,
                provider_cost BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                provider_order_id TEXT,
                refund_status TEXT NOT NULL DEFAULT 'NONE',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)

        # =====================================================
        # MIGRATION
        # =====================================================

        db.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS last_checkin_at TEXT
        """)

        db.execute("""
            ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT '5sim'
        """)
        for col, typ in [("country_name", "TEXT"), ("service_name", "TEXT"), ("operator", "TEXT"), ("phone", "TEXT"), ("expires_at", "TEXT")]:
            db.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {col} {typ}")


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
    first_name=None
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
                SET
                    username = %s,
                    first_name = %s
                WHERE telegram_id = %s
                """,
                (
                    username,
                    first_name,
                    telegram_id
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
            VALUES
            (%s,%s,%s,0,%s)
            """,
            (
                telegram_id,
                username,
                first_name,
                now()
            )
        )


# =========================================================
# GET USER
# =========================================================

def get_user(
    telegram_id
):

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

def get_balance(
    telegram_id
):

    user = get_user(
        telegram_id
    )

    if not user:

        create_user(
            telegram_id=telegram_id
        )

        return 0

    return user["balance"]


# =========================================================
# GET TOTAL USER
# =========================================================

def get_total_users():

    with get_db() as db:

        result = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM users
            """
        ).fetchone()

        return result["total"]


# =========================================================
# DEPOSIT HISTORY
# =========================================================

def get_deposit_history(
    telegram_id,
    limit=5
):

    with get_db() as db:

        return db.execute(
            """
            SELECT *
            FROM deposits
            WHERE telegram_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (
                telegram_id,
                limit
            )
        ).fetchall()


# =========================================================
# ORDER HISTORY
# =========================================================

def get_order_history(
    telegram_id,
    limit=5
):

    with get_db() as db:

        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE telegram_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (
                telegram_id,
                limit
            )
        ).fetchall()


# =========================================================
# ADD BALANCE
# =========================================================

def add_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None
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

        after = (
            before +
            amount
        )

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id
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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s)
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


# =========================================================
# SUBTRACT BALANCE
# =========================================================

def subtract_balance(
    telegram_id,
    amount,
    transaction_type,
    reference=None,
    description=None
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

        after = (
            before -
            amount
        )

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id
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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s)
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


# =========================================================
# COMPLETE DEPOSIT
# =========================================================

def complete_deposit(
    deposit_id,
    payment_reference=None
):

    with get_db() as db:

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

        telegram_id = deposit[
            "telegram_id"
        ]

        amount = int(
            deposit["amount"]
        )

        status = str(
            deposit["status"]
        ).upper()

        if status == "SUCCESS":

            user = db.execute(
                """
                SELECT balance
                FROM users
                WHERE telegram_id = %s
                """,
                (telegram_id,)
            ).fetchone()

            return (
                False,
                telegram_id,
                amount,
                user["balance"]
                if user
                else 0
            )

        if status != "PENDING":

            user = db.execute(
                """
                SELECT balance
                FROM users
                WHERE telegram_id = %s
                """,
                (telegram_id,)
            ).fetchone()

            current_balance = (
                user["balance"]
                if user
                else 0
            )

            return (
                False,
                telegram_id,
                amount,
                current_balance
            )

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

        before = int(
            user["balance"]
        )

        after = (
            before +
            amount
        )

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                after,
                telegram_id
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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                telegram_id,
                amount,
                before,
                after,
                "DEPOSIT",
                deposit_id,
                "Deposit berhasil dikonfirmasi.",
                now()
            )
        )

        db.execute(
            """
            UPDATE deposits
            SET
                status = 'SUCCESS',
                payment_reference = %s,
                completed_at = %s
            WHERE deposit_id = %s
            AND status = 'PENDING'
            """,
            (
                payment_reference,
                now(),
                deposit_id
            )
        )

        return (
            True,
            telegram_id,
            amount,
            after
        )


# =========================================================
# CREATE PENDING OTP ORDER
# =========================================================

def create_pending_order(
    telegram_id,
    order_id,
    country,
    service,
    sell_price,
    provider="5sim",
    country_name=None,
    service_name=None,
    operator=None
):

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

        balance_before = int(
            user["balance"]
        )

        if balance_before < sell_price:

            raise ValueError(
                "Saldo tidak cukup."
            )

        balance_after = (
            balance_before -
            sell_price
        )

        # -------------------------------------------------
        # POTONG SALDO
        # -------------------------------------------------

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                balance_after,
                telegram_id
            )
        )

        # -------------------------------------------------
        # LEDGER
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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                telegram_id,
                -sell_price,
                balance_before,
                balance_after,
                "ORDER_OTP",
                order_id,
                f"Order OTP {service}",
                now()
            )
        )

        # -------------------------------------------------
        # ORDER
        # -------------------------------------------------

        db.execute(
            """
            INSERT INTO orders
            (
                order_id,
                telegram_id,
                country, country_name,
                service, service_name,
                provider, operator,
                sell_price,
                provider_cost,
                status,
                provider_order_id,
                refund_status,
                created_at
            )
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                order_id,
                telegram_id,
                country, country_name,
                service, service_name,
                provider, operator,
                sell_price,
                0,
                "PENDING",
                None,
                "NONE",
                now()
            )
        )

        return balance_after


# =========================================================
# SAVE PROVIDER ORDER
# =========================================================

def save_provider_order(
    order_id,
    provider_order_id,
    provider_cost,
    phone=None,
    expires_at=None
):

    with get_db() as db:

        db.execute(
            """
            UPDATE orders
            SET
                provider_order_id = %s,
                provider_cost = %s,
                phone = COALESCE(%s, phone),
                expires_at = COALESCE(%s, expires_at)
            WHERE order_id = %s
            """,
            (
                str(provider_order_id),
                int(provider_cost),
                phone,
                expires_at,
                order_id
            )
        )


# =========================================================
# OTP PRICE QUOTES
# =========================================================

def save_otp_quote(
    quote_id,
    telegram_id,
    provider,
    country,
    service,
    operator=None,
    pool=None,
    cost_usd=0,
    stock=0,
    country_name=None
):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO otp_quotes
            (quote_id, telegram_id, provider, country, country_name, service, operator, pool, cost_usd, stock, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (quote_id) DO UPDATE SET
                telegram_id=EXCLUDED.telegram_id,
                provider=EXCLUDED.provider,
                country=EXCLUDED.country,
                country_name=EXCLUDED.country_name,
                service=EXCLUDED.service,
                operator=EXCLUDED.operator,
                pool=EXCLUDED.pool,
                cost_usd=EXCLUDED.cost_usd,
                stock=EXCLUDED.stock,
                created_at=EXCLUDED.created_at
            """,
            (quote_id, telegram_id, provider, country, country_name, service, operator, pool, float(cost_usd), int(stock), now())
        )


def get_otp_quote(quote_id, telegram_id=None):
    with get_db() as db:
        if telegram_id is None:
            return db.execute(
                "SELECT * FROM otp_quotes WHERE quote_id = %s",
                (quote_id,)
            ).fetchone()
        return db.execute(
            "SELECT * FROM otp_quotes WHERE quote_id = %s AND telegram_id = %s",
            (quote_id, telegram_id)
        ).fetchone()


# =========================================================
# GET ORDER
# =========================================================

def get_order(
    order_id
):

    with get_db() as db:

        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE order_id = %s
            """,
            (order_id,)
        ).fetchone()


# =========================================================
# MARK ORDER SUCCESS
# =========================================================

def mark_order_success(
    order_id
):

    with get_db() as db:

        result = db.execute(
            """
            UPDATE orders
            SET
                status = 'SUCCESS',
                completed_at = %s
            WHERE order_id = %s
            AND status = 'PENDING'
            """,
            (
                now(),
                order_id
            )
        )

        return (
            result.rowcount > 0
        )


# =========================================================
# REFUND ORDER
# =========================================================

def refund_order(
    order_id,
    reason="Refund order OTP"
):

    with get_db() as db:

        order = db.execute(
            """
            SELECT *
            FROM orders
            WHERE order_id = %s
            FOR UPDATE
            """,
            (order_id,)
        ).fetchone()

        if not order:

            raise ValueError(
                "Order tidak ditemukan."
            )

        # -------------------------------------------------
        # JANGAN REFUND 2X
        # -------------------------------------------------

        if (
            order["refund_status"]
            == "REFUNDED"
        ):

            user = db.execute(
                """
                SELECT balance
                FROM users
                WHERE telegram_id = %s
                """,
                (
                    order[
                        "telegram_id"
                    ],
                )
            ).fetchone()

            return {
                "refunded":
                    False,

                "already_refunded":
                    True,

                "balance":
                    user["balance"]
                    if user
                    else 0
            }

        if order["status"] == "SUCCESS":

            raise ValueError(
                "Order sudah berhasil dan tidak bisa direfund."
            )

        user = db.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id = %s
            FOR UPDATE
            """,
            (
                order[
                    "telegram_id"
                ],
            )
        ).fetchone()

        if not user:

            raise ValueError(
                "User tidak ditemukan."
            )

        balance_before = int(
            user["balance"]
        )

        balance_after = (
            balance_before +
            int(order["sell_price"])
        )

        # -------------------------------------------------
        # KEMBALIKAN SALDO
        # -------------------------------------------------

        db.execute(
            """
            UPDATE users
            SET balance = %s
            WHERE telegram_id = %s
            """,
            (
                balance_after,
                order[
                    "telegram_id"
                ]
            )
        )

        # -------------------------------------------------
        # LEDGER REFUND
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
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                order[
                    "telegram_id"
                ],
                order[
                    "sell_price"
                ],
                balance_before,
                balance_after,
                "ORDER_REFUND",
                order_id,
                reason,
                now()
            )
        )

        # -------------------------------------------------
        # UPDATE ORDER
        # -------------------------------------------------

        db.execute(
            """
            UPDATE orders
            SET
                status = 'REFUNDED',
                refund_status = 'REFUNDED',
                completed_at = %s
            WHERE order_id = %s
            """,
            (
                now(),
                order_id
            )
        )

        return {
            "refunded":
                True,

            "already_refunded":
                False,

            "balance":
                balance_after,

            "telegram_id":
                order[
                    "telegram_id"
                ],

            "amount":
                order[
                    "sell_price"
                ]
        }


# =========================================================
# CHECK-IN / SALDO GRATIS
# =========================================================

def get_checkin_status(telegram_id):
    """Return deposit eligibility and last check-in timestamp."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT
                u.last_checkin_at,
                COALESCE((
                    SELECT SUM(amount)
                    FROM deposits
                    WHERE telegram_id = %s
                      AND status = 'SUCCESS'
                ), 0) AS total_success_deposit,
                EXISTS(
                    SELECT 1 FROM deposits
                    WHERE telegram_id = %s
                      AND status = 'SUCCESS'
                      AND amount >= 10000
                ) AS has_min_deposit
            FROM users u
            WHERE u.telegram_id = %s
            """,
            (telegram_id, telegram_id, telegram_id)
        ).fetchone()
        return row or {"last_checkin_at": None, "total_success_deposit": 0, "has_min_deposit": False}


def claim_checkin(telegram_id, reward):
    """Atomically claim a daily check-in reward.

    Eligibility requires at least Rp10.000 in successful lifetime deposits
    and 24 hours since the previous claim.
    """
    from datetime import datetime, timezone, timedelta

    reward = int(reward)
    if reward < 1:
        raise ValueError("Reward tidak valid.")

    with get_db() as db:
        user = db.execute(
            """SELECT balance, last_checkin_at FROM users
               WHERE telegram_id = %s FOR UPDATE""",
            (telegram_id,)
        ).fetchone()
        if not user:
            raise ValueError("User belum terdaftar.")

        deposit_stats = db.execute(
            """SELECT
                    COALESCE(SUM(amount), 0) AS total,
                    EXISTS(SELECT 1 FROM deposits d2
                           WHERE d2.telegram_id = %s
                             AND d2.status = 'SUCCESS'
                             AND d2.amount >= 10000) AS eligible
               FROM deposits
               WHERE telegram_id = %s AND status = 'SUCCESS'""",
            (telegram_id, telegram_id)
        ).fetchone()
        total = deposit_stats["total"]
        if not deposit_stats["eligible"]:
            return {"ok": False, "reason": "MIN_DEPOSIT", "total_deposit": int(total or 0)}

        now_dt = datetime.now(timezone.utc)
        last_raw = user.get("last_checkin_at")
        if last_raw:
            try:
                last_dt = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed = now_dt - last_dt
                if elapsed < timedelta(hours=24):
                    remaining = timedelta(hours=24) - elapsed
                    total_seconds = max(0, int(remaining.total_seconds()))
                    return {
                        "ok": False,
                        "reason": "COOLDOWN",
                        "remaining_seconds": total_seconds,
                        "last_checkin_at": last_dt.isoformat(),
                    }
            except Exception:
                pass

        before = int(user["balance"] or 0)
        after = before + reward
        now_iso = now_dt.isoformat()
        db.execute(
            "UPDATE users SET balance = %s, last_checkin_at = %s WHERE telegram_id = %s",
            (after, now_iso, telegram_id)
        )
        db.execute(
            """INSERT INTO ledger
               (telegram_id, amount, balance_before, balance_after,
                transaction_type, reference, description, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                telegram_id, reward, before, after,
                "CHECKIN", f"CHECKIN:{telegram_id}:{now_iso}",
                "Saldo gratis check-in 24 jam", now_iso
            )
        )
        return {
            "ok": True,
            "reward": reward,
            "balance": after,
            "total_deposit": int(total or 0),
            "last_checkin_at": now_iso,
        }
