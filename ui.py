"""Arbiter Dashboard v2 — Streamlit UI (mobile-first, multi-asset)."""

import time
from datetime import datetime, timezone

import streamlit as st

import config
import db

st.set_page_config(
    page_title="Arbiter Dashboard",
    page_icon="⚖️",
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
        return "—"
    return f"${val:,.0f}"


def fmt_pct(val) -> str:
    if val is None:
        return "—"
    return f"{val:.2f}%"


def fmt_leverage(val) -> str:
    if val is None or val == 0:
        return "—"
    return f"{val:.2f}x"


# ── Init ────────────────────────────────────────────────────────────────────

db.init_db()
targets = db.get_portfolio_targets()
user = db.get_user_inputs()
positions = db.get_portfolio_positions()
rejected = db.get_rejected_markets()
has_data = targets.get("num_positions", 0) > 0

budget = user.get("budget", config.DEFAULT_BUDGET)
emergency = max(config.EMERGENCY_FLOOR, config.EMERGENCY_PCT * budget)
deployable = budget - emergency - config.OPS_RESERVE

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
            st.success("Saved — worker will recompute next cycle.")
            time.sleep(0.5)
            st.rerun()

    new_emergency = max(config.EMERGENCY_FLOOR, config.EMERGENCY_PCT * new_budget)
    new_deployable = new_budget - new_emergency - config.OPS_RESERVE
    st.caption(f"Emergency: {fmt_usd(new_emergency)}")
    st.caption(f"Deployable: {fmt_usd(new_deployable)}")

# ── Main Content ────────────────────────────────────────────────────────────

st.title("Arbiter Dashboard")

if not has_data:
    st.warning("Waiting for worker to fetch market data... Run `python worker.py`")
    st.stop()

# ── Portfolio Header ────────────────────────────────────────────────────────

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Portfolio Net APR", fmt_pct(targets.get("portfolio_net_apr")))
with c2:
    st.metric("Est. $/day", fmt_usd(targets.get("portfolio_usd_day")))
with c3:
    st.metric("Positions", str(targets.get("num_positions", 0)))

from engine.scanner import is_nyse_trading_hours
if is_nyse_trading_hours():
    st.success("NYSE OPEN — equity trades can execute now")
else:
    st.info("NYSE CLOSED — equity trades pending until next open")

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
    st.info("No positions — waiting for worker...")

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
        rej_rows = []
        for r in rejected:
            rej_rows.append({
                "Ticker": r["ticker"],
                "Reason": r["reason"],
                "Forecast APR": fmt_pct(r.get("forecast_apr")),
                "Score": fmt_pct(r.get("score")),
                "Cap $": fmt_usd(r.get("cap_final")),
            })
        st.dataframe(rej_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No rejected markets")

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
    st.info("Nexus Mutual cover is **discretionary** — members have final say on claims.")

    covers = db.get_insurance_covers()
    if covers:
        for cover in covers:
            expiry = cover.get("expiry_date", "")
            days_left = ""
            try:
                exp = datetime.fromisoformat(expiry).date()
                d = (exp - datetime.now(timezone.utc).date()).days
                days_left = f" — **EXPIRED**" if d < 0 else f" — {d}d left"
            except (ValueError, TypeError):
                pass
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"{cover['cover_type']} — {fmt_usd(cover['amount'])} (exp {expiry[:10]}){days_left}")
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
