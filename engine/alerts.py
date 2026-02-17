"""Pushover notifications + deduplication logic."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

import config
import db

log = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def _pushover_configured() -> bool:
    return bool(config.PUSHOVER_APP_TOKEN and config.PUSHOVER_USER_KEY)


def _send_pushover(title: str, message: str, priority: int = 0, **kwargs):
    """Send a Pushover notification.

    priority: -2 (silent), -1 (quiet), 0 (normal), 1 (high), 2 (emergency)
    Emergency (2) requires retry and expire parameters.
    """
    if not _pushover_configured():
        log.info("Pushover not configured — skipping: %s", title)
        return False

    payload = {
        "token": config.PUSHOVER_APP_TOKEN,
        "user": config.PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if priority == 2:
        payload["retry"] = kwargs.get("retry", 300)    # 5 min retry
        payload["expire"] = kwargs.get("expire", 3600)  # 1 hour expire
    payload.update(kwargs)

    try:
        resp = httpx.post(PUSHOVER_API_URL, data=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Pushover send failed: %s", e)
        return False


def _should_send(ticker: str, severity: str, advantage_apr: float = 0) -> bool:
    """Check deduplication rules before sending an alert."""
    last = db.get_last_alert(ticker, severity)
    if not last:
        return True

    last_time = datetime.fromisoformat(last["sent_at"])
    now = datetime.now(timezone.utc)

    if severity == "OPPORTUNITY":
        if now - last_time < timedelta(hours=config.OPPORTUNITY_DEDUP_HOURS):
            return False
    elif severity == "CRITICAL":
        if last.get("acknowledged"):
            return False
        if now - last_time < timedelta(minutes=config.CRITICAL_RESEND_MINUTES):
            return False

    return True


def send_info_alert(ticker: str, current_ticker: str, best_ema: float, current_ema: float):
    """Send INFO alert — best ticker approaching hurdle threshold."""
    if not _should_send(ticker, "INFO"):
        return
    gap = best_ema - current_ema
    msg = (
        f"Approaching opportunity: {ticker} EMA APR {best_ema:.1f}% is within "
        f"{config.FUNDING_HURDLE_APR_POINTS - gap:.1f} APR points of switching from {current_ticker}."
    )
    _send_pushover("Arbiter INFO", msg, priority=-1)
    db.insert_alert(ticker, "INFO", msg)
    log.info("INFO alert: %s", msg)


def send_opportunity_alert(
    ticker: str, current_ticker: str, best_ema: float, current_ema: float,
    is_trading_hours: bool
):
    """Send OPPORTUNITY alert — hurdle condition met."""
    if not _should_send(ticker, "OPPORTUNITY", best_ema - current_ema):
        return
    advantage = best_ema - current_ema
    timing = "Execute now" if is_trading_hours else "Execute at next NYSE open"
    msg = (
        f"OPPORTUNITY: Switch from {current_ticker} to {ticker}. "
        f"EMA APR advantage: +{advantage:.1f} APR points "
        f"({ticker} {best_ema:.1f}% vs {current_ticker} {current_ema:.1f}%). "
        f"{timing}."
    )
    _send_pushover("Arbiter OPPORTUNITY", msg, priority=1)
    db.insert_alert(ticker, "OPPORTUNITY", msg)
    db.insert_opportunity(ticker, best_ema, advantage)
    log.info("OPPORTUNITY alert: %s", msg)


def send_critical_alert(ticker: str, reason: str):
    """Send CRITICAL alert — safety filter failure or persistent drift."""
    if not _should_send(ticker, "CRITICAL"):
        return
    msg = f"CRITICAL for {ticker}: {reason}"
    _send_pushover(
        "Arbiter CRITICAL",
        msg,
        priority=2,
        retry=300,
        expire=3600,
    )
    db.insert_alert(ticker, "CRITICAL", msg)
    log.warning("CRITICAL alert: %s", msg)


def check_insurance_expiry_alerts():
    """Check for insurance covers expiring within 7 days or already expired."""
    covers = db.get_insurance_covers()
    now = datetime.now(timezone.utc).date()

    for cover in covers:
        try:
            expiry = datetime.fromisoformat(cover["expiry_date"]).date()
        except (ValueError, TypeError):
            continue

        days_left = (expiry - now).days
        ticker_key = f"insurance_{cover['id']}"

        if days_left < 0:
            send_critical_alert(ticker_key, f"{cover['cover_type']} cover (${cover['amount']:,.0f}) EXPIRED")
        elif days_left <= 7:
            if not _should_send(ticker_key, "INFO"):
                continue
            msg = (
                f"{cover['cover_type']} cover (${cover['amount']:,.0f}) expires in "
                f"{days_left} day{'s' if days_left != 1 else ''}."
            )
            _send_pushover("Arbiter: Cover Expiring", msg, priority=0)
            db.insert_alert(ticker_key, "INFO", msg)
