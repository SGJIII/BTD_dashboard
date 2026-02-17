"""Tests for DB migration on legacy schema."""

import os
import sqlite3
import threading

import config
import db


def _setup_fresh_db(path: str):
    """Point db module at a fresh test DB."""
    if os.path.exists(path):
        os.remove(path)
    config.DB_PATH = path
    db._local = threading.local()


def _teardown(path: str):
    """Clean up test DB."""
    db._local = threading.local()
    if os.path.exists(path):
        os.remove(path)


def test_fresh_db_has_all_columns():
    """Fresh database should include all schema columns."""
    path = "data/test_fresh_cols.sqlite"
    try:
        _setup_fresh_db(path)
        db.init_db()
        conn = sqlite3.connect(path)

        pt_cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_targets)").fetchall()}
        rm_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejected_markets)").fetchall()}

        assert "run_status" in pt_cols
        assert "deep_scan_cohort" in pt_cols
        assert "instant_apr" in rm_cols
        assert "pre_rank" in rm_cols
        conn.close()
    finally:
        _teardown(path)


def test_legacy_db_migrates():
    """Old DB without new columns should gain them after init_db."""
    path = "data/test_legacy_migrate.sqlite"
    try:
        if os.path.exists(path):
            os.remove(path)

        # Create legacy schema WITHOUT new columns
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE portfolio_targets (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                num_positions INTEGER DEFAULT 0,
                health_status TEXT DEFAULT 'ACTION',
                updated_at TEXT
            );
            INSERT INTO portfolio_targets (id) VALUES (1);
            CREATE TABLE rejected_markets (
                coin TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                reason TEXT NOT NULL,
                forecast_apr REAL,
                updated_at TEXT
            );
            CREATE TABLE user_inputs (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                budget REAL NOT NULL DEFAULT 640000,
                updated_at TEXT
            );
            INSERT INTO user_inputs (id) VALUES (1);
        """)
        conn.close()

        # Verify columns missing
        conn = sqlite3.connect(path)
        pt_cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_targets)").fetchall()}
        assert "run_status" not in pt_cols
        conn.close()

        # Run init_db which should apply migrations
        _setup_fresh_db(path)
        config.DB_PATH = path
        db.init_db()

        conn = sqlite3.connect(path)
        pt_cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_targets)").fetchall()}
        rm_cols = {r[1] for r in conn.execute("PRAGMA table_info(rejected_markets)").fetchall()}

        assert "run_status" in pt_cols, "run_status not migrated"
        assert "deep_scan_cohort" in pt_cols, "deep_scan_cohort not migrated"
        assert "instant_apr" in rm_cols, "instant_apr not migrated"
        assert "pre_rank" in rm_cols, "pre_rank not migrated"
        conn.close()
    finally:
        _teardown(path)


def test_migration_idempotent():
    """Running init_db twice should not error."""
    path = "data/test_idempotent.sqlite"
    try:
        _setup_fresh_db(path)
        db.init_db()
        db._local = threading.local()
        db.init_db()  # second call should be no-op
    finally:
        _teardown(path)


def test_crud_after_migration():
    """CRUD operations should work after migration."""
    path = "data/test_crud.sqlite"
    try:
        _setup_fresh_db(path)
        db.init_db()

        # Write and read portfolio targets with new fields
        db.update_portfolio_targets(run_status="success", deep_scan_cohort=15)
        targets = db.get_portfolio_targets()
        assert targets["run_status"] == "success"
        assert targets["deep_scan_cohort"] == 15

        # Write and read rejected markets with new fields
        db.upsert_rejected_market("xyz:TEST", {
            "ticker": "TEST",
            "reason": "test reason",
            "instant_apr": 12.5,
            "pre_rank": 3,
        })
        rejected = db.get_rejected_markets()
        assert len(rejected) == 1
        assert rejected[0]["instant_apr"] == 12.5
        assert rejected[0]["pre_rank"] == 3
    finally:
        _teardown(path)
