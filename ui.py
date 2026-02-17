"""Arbiter Dashboard v2 — Streamlit UI (mobile-first, multi-asset)."""

import time
from datetime import datetime, timezone

import streamlit as st

import config
import db

st.set_page_config(
    page_title="Arbiter Dashboard",
    page_icon="\u2696\ufe0f",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    [data-testid="stMetric"] { background: #0e1117; border: 1px solid #262730;
        border-radius: 8px; padding: 12px; }
    [data-testid="stMetricValue"] { font-size: 1.4rem; }
    .ticker-big { font-size: 2.2rem; font-weight: bold; color: #4da6ff; }
    .stDataFrame { font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


def fmt_usd(val) -> str:
    if val is None:
        return "\u2014"
    return f"${val:,.0f}"


def fmt_pct(val) -> str:
    if val is None:
        return "\u2014"
    return f"{val:.2f}%"


def fmt_leverage(val) -> str:
    if val is None or val == 0:
        return "\u2014"
    return f"{val:.2f}x"


def _min_actionable_budget() -> float:
    """Estimate the minimum budget that produces H_max >= min_ticket."""
    b_low = (config.MIN_TICKET_USD * (1 + config.COLLATERAL_FRACTION)
             + config.EMERGENCY_FLOOR + config.OPS_RESERVE)
    b_high = config.MIN_TICKET_USD * (1 + config.COLLATERAL_FRACTION) / (
        1 - config.EMERGENCY_PCT - config.MIN_TICKET_BUDGET_PCT * (1 + config.COLLATERAL_FRACTION)
    ) + config.OPS_RESERVE
    return max(b_low, b_high)


def _render_rejected_table(rejected_list: list[dict], cohort_size: int):
    """Render the enhanced rejected markets table with APR transparency."""
    rej_rows = []
    for r in rejected_list:
        row = {
            "Ticker": r["ticker"],
            "Reason": r["reason"],
            "Instant APR": fmt_pct(r.get("instant_apr")),
            "Forecast APR": fmt_pct(r.get("forecast_apr")),
            "Score": fmt_pct(r.get("score")),
            "Cap $": fmt_usd(r.get("cap_final")),
        }
        pre_rank = r.get("pre_rank")
        if pre_rank is not None:
            row["Pre-Rank"] = str(pre_rank)
        else:
            row["Pre-Rank"] = "\u2014"
        rej_rows.append(row)
    st.dataframe(rej_rows, use_container_width=True, hide_index=True)
    if cohort_size > 0:
        st.caption(
            f"Deep-scanned top {cohort_size} markets by instantaneous funding. "
            f"Markets ranked below the cutoff were not deep-scanned (no forecast/score)."
        )


# ── Init ────────────────────────────────────────────────────────────────────

db.init_db()
targets = db.get_portfolio_targets()
user = db.get_user_inputs()
positions = db.get_portfolio_positions()
rejected = db.get_rejected_markets()

run_status = targets.get("run_status")  # None = never run, 'success'/'partial'/'no_candidates'
num_positions = targets.get("num_positions", 0)
deep_scan_cohort = targets.get("deep_scan_cohort", 0)
budget = user.get("budget", config.DEFAULT_BUDGET)

# ── Sidebar: Budget ─────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Budget")
    with st.form("budget"):
        new_budget = st.number_input(
            "Total Capital (USD)", min_value=0,
            value=int(budget), step=10000,
        )
        if st.form_submit_button("Update Budget"):
            db.update_user_inputs(budget=new_budget)
            with st.spinner("Recomputing portfolio with new budget..."):
                try:
                    from worker import market_refresh_job
                    market_refresh_job()
                    st.success("Saved and recomputed.")
                except Exception as e:
                    st.warning(f"Saved, but immediate recompute failed: {e}")
            st.rerun()

    preview = config.compute_budget_buckets(new_budget)
    new_emergency = preview["emergency"]
    new_deployable = preview["deployable"]
    new_h_max = preview["h_max"]
    new_min_ticket = preview["min_ticket"]
    st.caption(f"Emergency: {fmt_usd(new_emergency)}")
    st.caption(f"Deployable: {fmt_usd(new_deployable)}")
    st.caption(f"H_max: {fmt_usd(new_h_max)}")

    # Budget feasibility indicator
    if new_h_max < new_min_ticket:
        st.warning(
            f"Budget too small for any position. "
            f"Min ticket = {fmt_usd(new_min_ticket)}, but H_max = {fmt_usd(new_h_max)}. "
            f"Increase budget above ~{fmt_usd(_min_actionable_budget())} to enable allocation."
        )


# ── Main Content ────────────────────────────────────────────────────────────

st.title("Arbiter Dashboard")

# ── Empty State Handling ────────────────────────────────────────────────────

if run_status is None:
    # Worker has never run
    st.warning(
        "No data yet — the worker has not completed a scan. "
        "Run `python worker.py` to start."
    )
    st.stop()

if num_positions == 0:
    # Worker ran but produced zero positions — show diagnostics
    st.error("Worker completed but found **zero viable positions**.")

    # Zero-allocation reason card
    buckets = config.compute_budget_buckets(budget)
    h_max = buckets["h_max"]
    min_ticket = buckets["min_ticket"]

    with st.container():
        st.subheader("Why zero positions?")
        reasons = []
        if h_max < min_ticket:
            reasons.append(
                f"**Budget constraint**: H_max ({fmt_usd(h_max)}) < min ticket "
                f"({fmt_usd(min_ticket)}). No position can meet the minimum size."
            )
        if deep_scan_cohort == 0:
            reasons.append(
                "**No markets passed pre-filter**: All mapped markets had negative/zero "
                "instantaneous funding or failed hard gates."
            )
        elif not any(r.get("reason", "").startswith("negative funding forecast") for r in rejected):
            reasons.append(
                f"**Deep-scanned {deep_scan_cohort} markets** but none had positive "
                f"forecast APR after EMA smoothing."
            )

        # Check for specific rejection patterns
        nasdaq_fails = [r for r in rejected if "not in public directories" in r.get("reason", "")]
        neg_funding = [r for r in rejected if r.get("reason") == "negative/zero instantaneous funding"]
        neg_forecast = [r for r in rejected if r.get("reason") == "negative funding forecast"]

        if nasdaq_fails:
            reasons.append(
                f"**{len(nasdaq_fails)} market(s)** rejected: hedge symbol not found in "
                f"NASDAQ/NYSE directories."
            )
        if neg_forecast:
            tickers = ", ".join(r["ticker"] for r in neg_forecast[:5])
            reasons.append(
                f"**{len(neg_forecast)} market(s)** had negative funding forecast "
                f"after EMA smoothing ({tickers})."
            )

        if not reasons:
            reasons.append(
                "All eligible markets were either too small (below min ticket) or "
                "had insufficient funding data."
            )

        for r in reasons:
            st.markdown(f"- {r}")

        st.caption(
            f"Budget: {fmt_usd(budget)} | H_max: {fmt_usd(h_max)} | "
            f"Min ticket: {fmt_usd(min_ticket)} | Deep-scanned: {deep_scan_cohort} markets"
        )

    # Still show rejected table below for transparency
    st.divider()

    with st.expander(f"Rejected Markets ({len(rejected)})", expanded=True):
        if rejected:
            _render_rejected_table(rejected, deep_scan_cohort)
        else:
            st.info("No market data — worker may not have fetched markets yet.")

    st.stop()

# ── Portfolio Header ────────────────────────────────────────────────────────

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Portfolio Net APR", fmt_pct(targets.get("portfolio_net_apr")))
with c2:
    st.metric("Est. $/day", fmt_usd(targets.get("portfolio_usd_day")))
with c3:
    st.metric("Positions", str(num_positions))

from engine.scanner import is_nyse_trading_hours
if is_nyse_trading_hours():
    st.success("NYSE OPEN \u2014 equity trades can execute now")
else:
    st.info("NYSE CLOSED \u2014 equity trades pending until next open")

st.divider()

# ── Portfolio Positions Table ───────────────────────────────────────────────

st.subheader("Target Portfolio")

if positions:
    rows = []
    for p in positions:
        rows.append({
            "#": p["rank"],
            "Ticker": p["ticker"],
            "Hedge": p["hedge_symbol"],
            "Alloc $": fmt_usd(p["alloc_notional"]),
            "Alloc %": fmt_pct(p["alloc_pct"]),
            "Forecast APR": fmt_pct(p["forecast_apr"]),
            "Net APR": fmt_pct(p["net_apr"]),
            "Score": fmt_pct(p["score"]),
            "Binding Cap": (p.get("binding_cap") or "").upper(),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No positions \u2014 waiting for worker...")

st.divider()

# ── Totals Card ─────────────────────────────────────────────────────────────

st.subheader(f"Allocation Breakdown (Budget: {fmt_usd(budget)})")

col_a, col_b = st.columns(2)
with col_a:
    st.metric("Total Stock Long", fmt_usd(targets.get("total_hedge_notional")))
    st.metric("Total Perp Short", fmt_usd(targets.get("total_hedge_notional")))
    st.metric("Perp Collateral (USDC)", fmt_usd(targets.get("perp_collateral")))
with col_b:
    st.metric("Coinbase Treasury", fmt_usd(targets.get("coinbase_treasury")))
    st.metric("Coinbase Total", fmt_usd(targets.get("coinbase_total")))
    st.metric("Emergency Reserve", fmt_usd(targets.get("emergency")))

leverage = 1 / config.COLLATERAL_FRACTION if config.COLLATERAL_FRACTION > 0 else 0
st.caption(f"Perp leverage: {fmt_leverage(leverage)} (fixed at 1/{config.COLLATERAL_FRACTION})")

updated = targets.get("updated_at", "")[:19] if targets.get("updated_at") else ""
st.caption(f"Updated {updated}")

st.divider()

# ── Forecast Details (expander) ──────────────────────────────────────────────

with st.expander("Forecast Details"):
    if positions:
        forecast_rows = []
        for p in positions:
            forecast_rows.append({
                "Ticker": p["ticker"],
                "EMA 3d": fmt_pct(p.get("ema_3d")),
                "EMA 7d": fmt_pct(p.get("ema_7d")),
                "Weekend Mult": f"{p.get('weekend_mult', 1.0):.4f}",
                "Forecast APR": fmt_pct(p.get("forecast_apr")),
                "Slip Drag": fmt_pct(p.get("slippage_drag_apr")),
                "Fee Drag": fmt_pct(p.get("fee_drag_apr")),
                "Net Score": fmt_pct(p.get("score")),
            })
        st.dataframe(forecast_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No forecast data")

# ── Cap Details (expander) ───────────────────────────────────────────────────

with st.expander("Position Caps"):
    if positions:
        cap_rows = []
        for p in positions:
            cap_rows.append({
                "Ticker": p["ticker"],
                "Cap OI": fmt_usd(p.get("cap_oi")),
                "Cap Vol": fmt_usd(p.get("cap_vol")),
                "Cap Impact": fmt_usd(p.get("cap_impact")),
                "Cap Conc": fmt_usd(p.get("cap_conc")),
                "Cap Final": fmt_usd(p.get("cap_final")),
                "Binding": (p.get("binding_cap") or "").upper(),
                "Allocated": fmt_usd(p.get("alloc_notional")),
            })
        st.dataframe(cap_rows, use_container_width=True, hide_index=True)

# ── Rejected Markets (expander) ──────────────────────────────────────────────

with st.expander(f"Rejected Markets ({len(rejected)})"):
    if rejected:
        _render_rejected_table(rejected, deep_scan_cohort)
    else:
        st.info("No rejected markets")

st.divider()

# ── Implemented State & Drift Tracking ──────────────────────────────────────

with st.expander("Implemented State & Drift"):
    impl_positions = db.get_implemented_positions()
    impl_cash = db.get_implemented_cash()

    st.markdown("**Record your actual executed positions and cash allocations below.**")

    # Cash buckets form
    with st.form("impl_cash"):
        st.subheader("Cash Buckets")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            impl_collateral = st.number_input(
                "Perp Collateral (USDC)", min_value=0,
                value=int(impl_cash.get("perp_collateral", 0)), step=1000,
            )
        with cc2:
            impl_treasury = st.number_input(
                "Coinbase Treasury", min_value=0,
                value=int(impl_cash.get("coinbase_treasury", 0)), step=1000,
            )
        with cc3:
            impl_emergency = st.number_input(
                "Emergency Reserve", min_value=0,
                value=int(impl_cash.get("emergency_reserve", 0)), step=1000,
            )
        if st.form_submit_button("Save Cash"):
            db.update_implemented_cash(
                perp_collateral=impl_collateral,
                coinbase_treasury=impl_treasury,
                emergency_reserve=impl_emergency,
            )
            st.rerun()

    # Per-position form
    with st.form("add_impl_pos"):
        st.subheader("Add/Update Position")
        pc1, pc2, pc3, pc4 = st.columns(4)
        pos_coins = [p["coin"] for p in positions] if positions else []
        pos_labels = [f"{p['ticker']} ({p['hedge_symbol']})" for p in positions] if positions else []
        with pc1:
            if pos_labels:
                sel_idx = st.selectbox("Ticker", range(len(pos_labels)), format_func=lambda i: pos_labels[i])
            else:
                sel_idx = None
                st.text("No target positions")
        with pc2:
            impl_long = st.number_input("Long $ (stock)", min_value=0, value=0, step=1000)
        with pc3:
            impl_short = st.number_input("Short $ (perp)", min_value=0, value=0, step=1000)
        with pc4:
            pass
        if st.form_submit_button("Save Position") and sel_idx is not None and positions:
            p = positions[sel_idx]
            db.upsert_implemented_position(p["coin"], {
                "ticker": p["ticker"],
                "hedge_symbol": p["hedge_symbol"],
                "long_notional": impl_long,
                "short_notional": impl_short,
            })
            st.rerun()

    # Drift table
    if positions and (impl_positions or any(v for k, v in impl_cash.items() if k != "id" and k != "updated_at")):
        st.subheader("Target vs Implemented Drift")

        impl_map = {ip["coin"]: ip for ip in impl_positions}

        drift_rows = []
        for p in positions:
            coin = p["coin"]
            ip = impl_map.get(coin, {})
            target_alloc = p["alloc_notional"]
            actual_long = ip.get("long_notional", 0)
            actual_short = ip.get("short_notional", 0)
            drift_long = actual_long - target_alloc
            drift_short = actual_short - target_alloc
            drift_rows.append({
                "Ticker": p["ticker"],
                "Hedge": p["hedge_symbol"],
                "Target $": fmt_usd(target_alloc),
                "Long $": fmt_usd(actual_long),
                "Long Drift": fmt_usd(drift_long),
                "Short $": fmt_usd(actual_short),
                "Short Drift": fmt_usd(drift_short),
            })

        # Positions not in target (orphans)
        target_coins = {p["coin"] for p in positions}
        for ip in impl_positions:
            if ip["coin"] not in target_coins:
                drift_rows.append({
                    "Ticker": ip["ticker"] + " *",
                    "Hedge": ip["hedge_symbol"],
                    "Target $": fmt_usd(0),
                    "Long $": fmt_usd(ip["long_notional"]),
                    "Long Drift": fmt_usd(ip["long_notional"]),
                    "Short $": fmt_usd(ip["short_notional"]),
                    "Short Drift": fmt_usd(ip["short_notional"]),
                })

        st.dataframe(drift_rows, use_container_width=True, hide_index=True)

        # Cash drift
        target_collateral = targets.get("perp_collateral", 0)
        target_treasury = targets.get("coinbase_treasury", 0)
        target_emergency = targets.get("emergency", 0)
        ac = impl_cash.get("perp_collateral", 0)
        at = impl_cash.get("coinbase_treasury", 0)
        ae = impl_cash.get("emergency_reserve", 0)

        st.markdown("**Cash Drift**")
        cash_drift = [
            {"Bucket": "Perp Collateral", "Target": fmt_usd(target_collateral), "Actual": fmt_usd(ac), "Drift": fmt_usd(ac - target_collateral)},
            {"Bucket": "Coinbase Treasury", "Target": fmt_usd(target_treasury), "Actual": fmt_usd(at), "Drift": fmt_usd(at - target_treasury)},
            {"Bucket": "Emergency Reserve", "Target": fmt_usd(target_emergency), "Actual": fmt_usd(ae), "Drift": fmt_usd(ae - target_emergency)},
        ]
        st.dataframe(cash_drift, use_container_width=True, hide_index=True)

        # Action checklist
        st.markdown("**Action Checklist**")
        actions = []
        for p in positions:
            coin = p["coin"]
            ip = impl_map.get(coin, {})
            target = p["alloc_notional"]
            al = ip.get("long_notional", 0)
            ash_ = ip.get("short_notional", 0)
            if abs(al - target) > 100:
                direction = "BUY" if al < target else "SELL"
                actions.append(f"- {direction} {fmt_usd(abs(al - target))} of **{p['hedge_symbol']}** (long)")
            if abs(ash_ - target) > 100:
                direction = "SHORT" if ash_ < target else "COVER"
                actions.append(f"- {direction} {fmt_usd(abs(ash_ - target))} of **{p['ticker']}** (perp)")

        if abs(ac - target_collateral) > 100:
            direction = "ADD" if ac < target_collateral else "WITHDRAW"
            actions.append(f"- {direction} {fmt_usd(abs(ac - target_collateral))} perp collateral")
        if abs(at - target_treasury) > 100:
            direction = "DEPOSIT" if at < target_treasury else "WITHDRAW"
            actions.append(f"- {direction} {fmt_usd(abs(at - target_treasury))} Coinbase treasury")

        if actions:
            for a in actions:
                st.markdown(a)
        else:
            st.success("Portfolio matches target — no actions needed.")

        impl_updated = impl_cash.get("updated_at", "")[:19] if impl_cash.get("updated_at") else ""
        if impl_updated:
            st.caption(f"Implemented state last updated: {impl_updated}")
    else:
        st.info("Enter your implemented positions and cash above to see drift analysis.")

    # Remove position button
    if impl_positions:
        st.markdown("---")
        rm_labels = [f"{ip['ticker']} ({ip['hedge_symbol']})" for ip in impl_positions]
        rm_idx = st.selectbox("Remove position", range(len(rm_labels)), format_func=lambda i: rm_labels[i], key="rm_impl")
        if st.button("Remove"):
            db.delete_implemented_position(impl_positions[rm_idx]["coin"])
            st.rerun()

st.divider()

# ── Insurance Manager ───────────────────────────────────────────────────────

with st.expander("Insurance Manager"):
    ins_pct = config.DEFAULT_INSURANCE_BUDGET_PCT
    annual = budget * ins_pct / 100
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Annual Budget", fmt_usd(annual))
    with c2:
        st.metric("Monthly Budget", fmt_usd(annual / 12))

    st.markdown("""
| Location | Cover Type |
|---|---|
| Coinbase USDC | **Custody Cover** |
| Hyperliquid collateral | **Protocol Cover** |
""")
    st.info("Nexus Mutual cover is **discretionary** \u2014 members have final say on claims.")

    covers = db.get_insurance_covers()
    if covers:
        for cover in covers:
            expiry = cover.get("expiry_date", "")
            days_left = ""
            try:
                exp = datetime.fromisoformat(expiry).date()
                d = (exp - datetime.now(timezone.utc).date()).days
                days_left = f" \u2014 **EXPIRED**" if d < 0 else f" \u2014 {d}d left"
            except (ValueError, TypeError):
                pass
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"{cover['cover_type']} \u2014 {fmt_usd(cover['amount'])} (exp {expiry[:10]}){days_left}")
            with c2:
                if st.button("Remove", key=f"del_{cover['id']}"):
                    db.delete_insurance_cover(cover["id"])
                    st.rerun()

    with st.form("add_cover"):
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            ctype = st.selectbox("Type", ["Protocol Cover", "Custody Cover"])
        with ac2:
            camt = st.number_input("Amount", min_value=0, value=50000, step=5000)
        with ac3:
            cexp = st.date_input("Expiry")
        if st.form_submit_button("Add"):
            db.insert_insurance_cover(ctype, camt, str(cexp))
            st.rerun()

# ── Auto-refresh ────────────────────────────────────────────────────────────

if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()
if time.time() - st.session_state.last_refresh > 30:
    st.session_state.last_refresh = time.time()
    st.rerun()
