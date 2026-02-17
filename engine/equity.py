"""US equity validation via NASDAQ symbol directories."""

from __future__ import annotations

import logging
import threading
import time

import httpx

import config

log = logging.getLogger(__name__)

# Thread-safe cache for public symbols
_symbols_lock = threading.Lock()
_symbols_cache: set[str] = set()
_symbols_last_fetched: float = 0
_SYMBOLS_TTL = 86400       # refresh once per day on success
_SYMBOLS_RETRY_TTL = 300   # retry after 5 min on failure


def _fetch_symbol_file(url: str) -> set[str]:
    """Download a NASDAQ symbol directory file and return the set of tickers."""
    symbols = set()
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        for line in lines[1:]:
            if line.startswith("File Creation Time"):
                break
            parts = line.split("|")
            if parts:
                sym = parts[0].strip()
                if sym and sym.isalpha():
                    symbols.add(sym.upper())
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
    return symbols


def refresh_public_symbols() -> bool:
    """Download NASDAQ + other-listed symbol directories and cache them.

    Returns True if any symbols were loaded.
    Always updates _symbols_last_fetched to prevent rapid retries on failure.
    """
    global _symbols_cache, _symbols_last_fetched
    nasdaq = _fetch_symbol_file(config.NASDAQ_LISTED_URL)
    other = _fetch_symbol_file(config.OTHER_LISTED_URL)
    combined = nasdaq | other
    with _symbols_lock:
        # Always update timestamp to prevent retry storms
        _symbols_last_fetched = time.time()
        if combined:
            _symbols_cache = combined
            log.info("Loaded %d public symbols", len(_symbols_cache))
            return True
        else:
            log.warning("NASDAQ symbol fetch returned 0 symbols — keeping old cache (retry in 5 min)")
            return False


def is_public_equity(ticker: str) -> bool:
    """Check if ticker is in the public symbol directories.

    Fail-open: if directories can't be fetched and cache is empty,
    returns True (our HEDGE_MAP is curated, so assume valid).
    """
    with _symbols_lock:
        age = time.time() - _symbols_last_fetched if _symbols_last_fetched > 0 else float("inf")
        has_cache = bool(_symbols_cache)
        ttl = _SYMBOLS_TTL if has_cache else _SYMBOLS_RETRY_TTL

        if _symbols_last_fetched > 0 and age <= ttl:
            if has_cache:
                return ticker.upper() in _symbols_cache
            else:
                # Recently failed, don't retry — fail open
                return True

    # Need to refresh (first call or TTL expired)
    refresh_public_symbols()

    with _symbols_lock:
        if _symbols_cache:
            return ticker.upper() in _symbols_cache

    # Fail-open: can't verify, assume valid since HEDGE_MAP is curated
    return True
