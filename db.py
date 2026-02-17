"""Arbiter Dashboard v2 — SQLite persistence layer (WAL mode)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import logging

import config

log = logging.getLogger(__name__)

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local connection with WAL mode enabled."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


@contextmanager
def get_db():
    """Context manager yielding a sqlite3 connection."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Create all tables if they don't exist, then apply migrations."""
    with get_db() as conn:
        conn.executescript(_SCHEMA)
    _run_migrations()


# ── Migrations ─────────────────────────────────────────────────────────────

_MIGRATIONS: list[tuple[str, str, str]] = [
    ("portfolio_targets", "run_status", "TEXT"),
    ("portfolio_targets", "deep_scan_cohort", "INTEGER DEFAULT 0"),
    ("rejected_markets", "instant_apr", "REAL"),
    ("rejected_markets", "pre_rank", "INTEGER"),
]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] if isinstance(r, tuple) else r["name"] for r in rows}


def _run_migrations():
    """Apply pending ALTER TABLE ADD COLUMN migrations (idempotent)."""
    with get_db() as conn:
        cache: dict[str, set[str]] = {}
        applied = 0
        for table, column, col_def in _MIGRATIONS:
            if table not in cache:
                cache[table] = _table_columns(conn, table)
            if column in cache[table]:
                continue
            stmt = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            conn.execute(stmt)
            cache[table].add(column)
            applied += 1
            log.info("Migration: %s", stmt)
        if applied:
            log.info("Applied %d migration(s)", applied)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_inputs (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    budget   REAL NOT NULL DEFAULT 640000,
    updated_at TEXT
);

INSERT OR IGNORE INTO user_inputs (id) VALUES (1);

CREATE TABLE IF NOT EXISTS market_snapshots (
    ticker       TEXT PRIMARY KEY,
    coin         TEXT,
    mark_px      REAL,
    mid_px       REAL,
    funding_hourly REAL,
    funding_apr  REAL,
    oi           REAL,
    oi_usd       REAL,
    volume_24h   REAL,
    max_leverage REAL,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS funding_history (
    ticker     TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    funding_apr  REAL NOT NULL,
    PRIMARY KEY (ticker, timestamp)
);

CREATE TABLE IF NOT EXISTS funding_epochs_8h (
    coin       TEXT NOT NULL,
    epoch_ts   TEXT NOT NULL,
    rate_8h    REAL NOT NULL,
    apr        REAL NOT NULL,
    is_weekend INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (coin, epoch_ts)
);

CREATE TABLE IF NOT EXISTS ema_cache (
    ticker     TEXT PRIMARY KEY,
    ema_3d     REAL,
    ema_7d     REAL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_targets (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    num_positions         INTEGER DEFAULT 0,
    total_hedge_notional  REAL DEFAULT 0,
    perp_collateral       REAL DEFAULT 0,
    coinbase_treasury     REAL DEFAULT 0,
    coinbase_total        REAL DEFAULT 0,
    emergency             REAL DEFAULT 0,
    portfolio_net_apr     REAL DEFAULT 0,
    portfolio_usd_day     REAL DEFAULT 0,
    health_status         TEXT DEFAULT 'ACTION',
    run_status            TEXT,
    deep_scan_cohort      INTEGER DEFAULT 0,
    updated_at            TEXT
);

INSERT OR IGNORE INTO portfolio_targets (id) VALUES (1);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    coin              TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    hedge_symbol      TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    alloc_notional    REAL NOT NULL,
    alloc_pct         REAL NOT NULL,
    cap_oi            REAL,
    cap_vol           REAL,
    cap_impact        REAL,
    cap_conc          REAL,
    cap_final         REAL,
    binding_cap       TEXT,
    forecast_apr      REAL,
    net_apr           REAL,
    slippage_drag_apr REAL,
    fee_drag_apr      REAL,
    score             REAL,
    ema_3d            REAL,
    ema_7d            REAL,
    weekend_mult      REAL,
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS rejected_markets (
    coin          TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    instant_apr   REAL,
    forecast_apr  REAL,
    score         REAL,
    cap_final     REAL,
    pre_rank      INTEGER,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS alert_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT,
    severity     TEXT NOT NULL,
    message      TEXT NOT NULL,
    sent_at      TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS insurance_covers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL DEFAULT 'Nexus Mutual',
    cover_type  TEXT NOT NULL,
    amount      REAL NOT NULL,
    expiry_date TEXT NOT NULL,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS opportunity_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    ema_apr      REAL NOT NULL,
    advantage_apr REAL NOT NULL,
    triggered_at TEXT
);

CREATE TABLE IF NOT EXISTS implemented_positions (
    coin            TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    hedge_symbol    TEXT NOT NULL,
    long_notional   REAL NOT NULL DEFAULT 0,
    short_notional  REAL NOT NULL DEFAULT 0,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS implemented_cash (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    perp_collateral     REAL NOT NULL DEFAULT 0,
    coinbase_treasury   REAL NOT NULL DEFAULT 0,
    emergency_reserve   REAL NOT NULL DEFAULT 0,
    updated_at          TEXT
);

INSERT OR IGNORE INTO implemented_cash (id) VALUES (1);
"""


# ── User Inputs CRUD ────────────────────────────────────────────────────────

def get_user_inputs() -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM user_inputs WHERE id = 1").fetchone()
        return dict(row) if row else {}


def update_user_inputs(**kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values())
    with get_db() as conn:
        conn.execute(f"UPDATE user_inputs SET {sets} WHERE id = 1", vals)


# ── Market Snapshots CRUD ───────────────────────────────────────────────────

def upsert_market_snapshot(ticker: str, data: dict):
    data["ticker"] = ticker
    data["updated_at"] = _now()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conflict = ", ".join(f"{k} = excluded.{k}" for k in data if k != "ticker")
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO market_snapshots ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(ticker) DO UPDATE SET {conflict}",
            list(data.values()),
        )


def get_market_snapshot(ticker: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ?", (ticker,)
        ).fetchone()
        return dict(row) if row else None


def get_all_market_snapshots() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_snapshots ORDER BY funding_apr DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Funding History CRUD ────────────────────────────────────────────────────

def upsert_funding_history(ticker: str, timestamp: str, rate: float, apr: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO funding_history (ticker, timestamp, funding_rate, funding_apr) "
            "VALUES (?, ?, ?, ?)",
            (ticker, timestamp, rate, apr),
        )


def get_funding_history(ticker: str, limit: int = 200) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM funding_history WHERE ticker = ? ORDER BY timestamp ASC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Funding Epochs 8h CRUD ──────────────────────────────────────────────────

def upsert_funding_epoch_8h(coin: str, epoch_ts: str, rate_8h: float, apr: float, is_weekend: bool):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO funding_epochs_8h (coin, epoch_ts, rate_8h, apr, is_weekend) "
            "VALUES (?, ?, ?, ?, ?)",
            (coin, epoch_ts, rate_8h, apr, 1 if is_weekend else 0),
        )


def get_funding_epochs_8h(coin: str, limit: int = 84) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM funding_epochs_8h WHERE coin = ? ORDER BY epoch_ts ASC LIMIT ?",
            (coin, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── EMA Cache CRUD ──────────────────────────────────────────────────────────

def upsert_ema(ticker: str, ema_3d: float, ema_7d: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ema_cache (ticker, ema_3d, ema_7d, updated_at) VALUES (?, ?, ?, ?)",
            (ticker, ema_3d, ema_7d, _now()),
        )


def get_ema(ticker: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT ema_3d, ema_7d FROM ema_cache WHERE ticker = ?", (ticker,)
        ).fetchone()
        return dict(row) if row else None


def get_all_emas() -> dict[str, dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT ticker, ema_3d, ema_7d FROM ema_cache").fetchall()
        return {r["ticker"]: {"ema_3d": r["ema_3d"], "ema_7d": r["ema_7d"]} for r in rows}


# ── Portfolio Targets CRUD ──────────────────────────────────────────────────

def update_portfolio_targets(**kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values())
    with get_db() as conn:
        conn.execute(f"UPDATE portfolio_targets SET {sets} WHERE id = 1", vals)


def get_portfolio_targets() -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM portfolio_targets WHERE id = 1").fetchone()
        return dict(row) if row else {}


# ── Portfolio Positions CRUD ────────────────────────────────────────────────

def clear_portfolio_positions():
    with get_db() as conn:
        conn.execute("DELETE FROM portfolio_positions")


def upsert_portfolio_position(coin: str, data: dict):
    data["coin"] = coin
    data["updated_at"] = _now()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conflict = ", ".join(f"{k} = excluded.{k}" for k in data if k != "coin")
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO portfolio_positions ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(coin) DO UPDATE SET {conflict}",
            list(data.values()),
        )


def get_portfolio_positions() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_positions ORDER BY rank ASC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Rejected Markets CRUD ──────────────────────────────────────────────────

def clear_rejected_markets():
    with get_db() as conn:
        conn.execute("DELETE FROM rejected_markets")


def upsert_rejected_market(coin: str, data: dict):
    data["coin"] = coin
    data["updated_at"] = _now()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conflict = ", ".join(f"{k} = excluded.{k}" for k in data if k != "coin")
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO rejected_markets ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(coin) DO UPDATE SET {conflict}",
            list(data.values()),
        )


def get_rejected_markets() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM rejected_markets ORDER BY forecast_apr DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Alert History CRUD ──────────────────────────────────────────────────────

def insert_alert(ticker: str | None, severity: str, message: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO alert_history (ticker, severity, message, sent_at) VALUES (?, ?, ?, ?)",
            (ticker, severity, message, _now()),
        )


def get_last_alert(ticker: str, severity: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM alert_history WHERE ticker = ? AND severity = ? ORDER BY sent_at DESC LIMIT 1",
            (ticker, severity),
        ).fetchone()
        return dict(row) if row else None


def get_unacknowledged_criticals() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_history WHERE severity = 'CRITICAL' AND acknowledged = 0 ORDER BY sent_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def acknowledge_alert(alert_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE alert_history SET acknowledged = 1 WHERE id = ?", (alert_id,)
        )


# ── Insurance Covers CRUD ──────────────────────────────────────────────────

def insert_insurance_cover(cover_type: str, amount: float, expiry_date: str, provider: str = "Nexus Mutual"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO insurance_covers (provider, cover_type, amount, expiry_date, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, cover_type, amount, expiry_date, _now()),
        )


def get_insurance_covers() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM insurance_covers ORDER BY expiry_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_insurance_cover(cover_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM insurance_covers WHERE id = ?", (cover_id,))


# ── Opportunity Log CRUD ────────────────────────────────────────────────────

def insert_opportunity(ticker: str, ema_apr: float, advantage_apr: float):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO opportunity_log (ticker, ema_apr, advantage_apr, triggered_at) VALUES (?, ?, ?, ?)",
            (ticker, ema_apr, advantage_apr, _now()),
        )


# ── Implemented Positions CRUD ────────────────────────────────────────────

def upsert_implemented_position(coin: str, data: dict):
    data["coin"] = coin
    data["updated_at"] = _now()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conflict = ", ".join(f"{k} = excluded.{k}" for k in data if k != "coin")
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO implemented_positions ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(coin) DO UPDATE SET {conflict}",
            list(data.values()),
        )


def get_implemented_positions() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM implemented_positions ORDER BY coin ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_implemented_position(coin: str):
    with get_db() as conn:
        conn.execute("DELETE FROM implemented_positions WHERE coin = ?", (coin,))


def clear_implemented_positions():
    with get_db() as conn:
        conn.execute("DELETE FROM implemented_positions")


# ── Implemented Cash CRUD ─────────────────────────────────────────────────

def update_implemented_cash(**kwargs):
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values())
    with get_db() as conn:
        conn.execute(f"UPDATE implemented_cash SET {sets} WHERE id = 1", vals)


def get_implemented_cash() -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM implemented_cash WHERE id = 1").fetchone()
        return dict(row) if row else {}
