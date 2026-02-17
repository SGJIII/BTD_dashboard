Below is a **fully explicit (no “x or y”), dev-ready** rewrite of your PRD for the **Arbiter Dashboard**, updated to use **Option 2 (conservative collateral fraction heuristic; no Hyperliquid account connection required)** and to run **locally + continuously + persist state across restarts**.

---

# Product Requirements Document (PRD): Arbiter Dashboard

| Field     | Value                                                                                                                                                                                                                                                                                                         |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Version   | 1.1                                                                                                                                                                                                                                                                                                           |
| Status    | Dev Ready                                                                                                                                                                                                                                                                                                     |
| Owner     | Ed Johnson                                                                                                                                                                                                                                                                                                    |
| Type      | Local, mobile-first web app (Streamlit UI + local worker daemon)                                                                                                                                                                                                                                              |
| Objective | Provide **real-time monitoring**, **opportunity alerts**, and **deterministic target allocations** for a **manual** delta-neutral cash-and-carry strategy using Hyperliquid perps + US-listed equities, optimized for **risk-adjusted net APR** with **zero liquidation tolerance** (heuristic safety model). |

---

## 1) Executive Summary

**Arbiter Dashboard** is a **Human-in-the-Loop intelligence console**. It **never executes trades** and **never moves money**. It continuously ingests:

* Hyperliquid perp market data (funding, OI, volume, mid/mark) via the Hyperliquid **`/info` endpoint** ([Hyperliquid Docs][1])
* US equity price/volume data (for validation and price divergence checks)
* User inputs (budget, buffers, current holding, Coinbase APR, insurance budget settings)

The dashboard computes an **Optimal Portfolio State**:

* **Stock long notional (1x; cash-only, no margin)**
* **Perp short notional**
* **Perp collateral required** to survive a **+15% adverse move** using a **conservative collateral fraction heuristic**
* **Coinbase USDC treasury allocation**
* **Estimated net APR** (funding income + Coinbase rewards − insurance drag − estimated fees)

It compares Optimal vs Current state and outputs **plain-English action items** such as:

* “Buy $XX of MSTR in brokerage”
* “Short $XX of MSTR-PERP on Hyperliquid”
* “Move $YY USDC from Coinbase → Perp collateral account (manual)”

Core philosophy: **Automated Intelligence, Manual Execution.**

---

## 2) Product Scope

### 2.1 In-Scope (v1.1)

1. Continuous market monitoring + trend smoothing of funding rates
2. Opportunity scanning and alerts (new best candidate)
3. Deterministic target allocation calculator (single-asset hedge)
4. Action-item generation (manual checklist)
5. Estimated net APR + estimated dollar returns
6. Insurance budget calculator + “which Nexus Mutual cover product to buy” recommendation
7. Local persistence (inputs + history + last computations) across restarts
8. Runs locally 24/7 as two local services: worker daemon + Streamlit UI

### 2.2 Out-of-Scope (hard non-goals)

1. No trade execution on any venue
2. No automated withdrawals, wires, or transfers
3. No Hyperliquid account connection required (no liquidation price reads; heuristic only)
4. No brokerage account connection required in MVP (current balances are manual inputs)
5. No multi-asset portfolio allocation (v1.1 is **one active hedge** at a time)

---

## 3) User Stories (Deterministic)

1. **Status at a glance:** As a user, I open the app on my phone and see a **single health status** (OPTIMIZED / ACTION / CRITICAL) plus the current estimated net APR.
2. **Exact math:** As a user, I see **exact target dollar amounts** for: Stock Long (1x), Perp Short, Perp Collateral, Coinbase USDC Treasury.
3. **Noise-filtered opportunities:** As a user, I get an alert only when a new asset’s **3-day EMA funding APR** exceeds my current asset by **≥ 20 APR points**.
4. **Simple manual workflow:** As a user, I get a checklist of action items that I can tick off and the dashboard updates “Current vs Target” drift.
5. **Insurance clarity:** As a user, I see my **monthly insurance budget** and the **specific Nexus Mutual cover product category** to purchase for each risk bucket, with coverage targets and reminders that cover is discretionary. ([docs.nexusmutual.io][2])

---

## 4) Definitions (Unambiguous)

* **NAV:** Total capital input by user (USD).
* **Emergency Buffer:** USD amount always held in Coinbase USDC and never deployed into the hedge.
* **Deployable Capital:** `NAV − EmergencyBuffer`.
* **Single-Asset Hedge:** One equity long (cash-only) + one perp short of the same ticker.
* **Funding 8h Rate:** The exchange’s 8-hour funding rate; Hyperliquid funding is **paid hourly at 1/8** of the computed 8h rate. ([Hyperliquid Docs][3])
* **Funding APR:** `fundingAPR = funding8hRate × 3 × 365`. (APR, not APY) ([Hyperliquid Docs][3])
* **3-Day EMA Funding APR:** EMA computed from the last **9 funding epochs** (3 days × 3 epochs/day).
* **OI USD:** `openInterest × markPrice` (USD open interest measure used for sizing caps; both values come from market context feeds). ([Chainstack][4])
* **5% OI Cap:** Max notional position size is 5% of OI USD.
* **Conservative Collateral Fraction Heuristic (Option 2):** A deterministic collateral requirement that approximates “survive +15% move” without reading liquidation price from the account.

---

## 5) Inputs (User Configuration) — Persisted

All user inputs are editable in the UI and **persisted in SQLite** (see Section 11).

### 5.1 Required Inputs (with defaults)

* **NAV (USD):** default `640000`
* **Emergency Buffer (USD):** default `50000`
* **Current Holding (ticker):** default `CRCL`
* **Coinbase USDC Rewards APR (%):** default `3.50`
  Rewards accrue daily and are typically distributed weekly per Coinbase terms. ([Coinbase Help][5])
* **Insurance Budget (annual % of NAV):** default `1.50`
* **Fees Model Toggle:** fixed to “include Hyperliquid taker fee assumption” (see Section 9.4); fee schedule is tiered but MVP uses a constant conservative estimate derived from the published fees page. ([Hyperliquid Docs][6])

### 5.2 Hard Risk Parameters (not user-editable in v1.1)

* **Min maxLeverage availability:** `>= 10x` (market eligibility filter)
* **Min Hyperliquid 24h notional volume:** `>= $5,000,000`
* **Max price divergence:** `<= 1.5%`
* **Max position size:** `<= 5% of OI USD`
* **Funding hurdle (APR points):** `20.0`
* **Perp collateral heuristic parameters:**

  * `adverse_move_pct = 0.15`
  * `maintenance_buffer_pct = 0.10`
  * `perp_ops_reserve_usd = 2500`
  * `min_collateral_fraction = adverse_move_pct + maintenance_buffer_pct = 0.25`

---

## 6) Data Sources (Definitive)

### 6.1 Hyperliquid Market Data (Required)

Use Hyperliquid **`/info`** endpoint. ([Hyperliquid Docs][1])

**Primary universe snapshot** (every 60 seconds):

* `type: "metaAndAssetCtxs"` to fetch:

  * mark price, mid price
  * current funding rate
  * open interest
  * 24h volume metrics
  * max leverage availability (from meta/universe)
    ([Chainstack][4])

**Funding history** (every 10 minutes, for top markets only):

* `type: "fundingHistory"` to retrieve funding time series and compute 3-day EMA for candidate selection (Section 7.2).
  Positive funding means **longs pay shorts**; negative means **shorts pay longs**. ([Quicknode][7])

### 6.2 US Equity Listing Validation (Hard Safety Gate)

Ticker “public status” is validated by downloading:

* `nasdaqlisted.txt` (Nasdaq-listed) ([NASDAQ Trader][8])
* `otherlisted.txt` (NYSE/other exchanges) ([NASDAQ Trader][9])

A ticker must appear in either file to be considered “public & hedgeable.”

### 6.3 Equity Price + Volume (Validation + Divergence)

Use a single Python market data provider library (implementation detail) to fetch:

* last price
* 24h/1d volume

This feed is used **only** for:

* price divergence check vs Hyperliquid mid price
* displaying equity price/volume in UI tables

---

## 7) Judgment Engine (Opportunity Scanning) — Explicit Algorithm

### 7.1 Scan Cadence (Worker daemon)

* Every **60 seconds**:

  * pull `metaAndAssetCtxs`
  * update live dashboard metrics (prices, OI, volume, instantaneous funding APR)
* Every **10 minutes**:

  * compute candidate list and opportunity decision

### 7.2 Candidate Discovery (Top-N funding focus; deterministic)

At each 10-minute scan:

1. From the latest `metaAndAssetCtxs`, compute instantaneous `fundingAPR_now` for all markets:

   * `fundingAPR_now = funding8hRate × 3 × 365` ([Hyperliquid Docs][3])
2. Sort markets by `fundingAPR_now` descending.
3. Take the top **N = 30** markets as the “Funding Focus Set.”
4. For each market in the Funding Focus Set, fetch `fundingHistory` and compute `fundingAPR_ema_3d` (Section 7.3).
5. Apply Minimum Viable Asset filters (Section 7.4). Any market that fails is removed.

### 7.3 3-Day EMA Funding APR (Exact math)

* Funding epochs per day: `3` (8h funding rate) ([Hyperliquid Docs][3])
* EMA window epochs: `9`
* `alpha = 2 / (9 + 1) = 0.2`
* Compute EMA over the most recent 9 `fundingAPR_epoch` values from `fundingHistory`.

### 7.4 Minimum Viable Asset Filters (“Safety Net”)

A market becomes **Eligible** only if ALL are true:

1. **Public Status:** ticker exists in Nasdaq symbol directories (Section 6.2). ([NASDAQ Trader][10])
2. **MaxLeverage Availability:** market metadata indicates **max leverage >= 10x** (eligibility only). ([Chainstack][4])
3. **Hyperliquid Liquidity:** 24h notional volume `>= $5,000,000` from market context. ([Chainstack][4])
4. **Price Divergence:** `abs(midPx_HL − px_equity) / px_equity <= 1.5%`
5. **OI-based Whale Safety:** later enforced by sizing (Section 8.3), but if OI USD is so small that 5% cap < $10,000, mark as “Not Tradable for size” and exclude.

### 7.5 Opportunity Trigger (Hurdle Rule; APR points)

Let:

* `cur = current_holding_ticker`
* `best = eligible_ticker_with_highest(fundingAPR_ema_3d)`

Trigger an OPPORTUNITY only if:

* `fundingAPR_ema_3d[best] >= fundingAPR_ema_3d[cur] + 20.0`

This is “APR points” (e.g., 80% → requires 100%+).

### 7.6 Trading Hours Gate (Recommendation only)

The dashboard **only recommends** executing equity trades during **NYSE core hours (9:30–16:00 ET)**. ([New York Stock Exchange][11])
If outside core hours, the dashboard sets the recommendation state to **PENDING UNTIL NEXT OPEN**.

---

## 8) Math Engine (Portfolio Targets) — Explicit Algorithm

### 8.1 Output Targets (always produced)

Given the **selected target ticker** `t` (either current holding or best opportunity if triggered), compute:

* `TargetStockLongUSD`
* `TargetPerpShortNotionalUSD`
* `TargetPerpCollateralUSD`
* `TargetCoinbaseTreasuryUSD`
* `PerpLeverage = TargetPerpShortNotionalUSD / TargetPerpCollateralUSD`
* `EstimatedNetAPR` and `EstimatedNet$/day`

### 8.2 Capital Buckets

* `NAV = user_input_nav`
* `EmergencyBuffer = user_input_emergency_buffer`
* `Deployable = NAV − EmergencyBuffer`

**EmergencyBuffer** is always assigned to Coinbase USDC.

### 8.3 Notional Sizing Constraints

Compute the maximum permissible notional `N_max` for ticker `t`:

1. **OI cap (5% rule):**

   * `OI_USD = openInterest × markPx`
   * `N_oi_cap = 0.05 × OI_USD` ([Chainstack][4])

2. **Collateral heuristic cap (Option 2):**
   Required collateral fraction:

   * `f = adverse_move_pct + maintenance_buffer_pct = 0.25`
   * `ops = perp_ops_reserve_usd = 2500`

   Collateral required for notional `N`:

   * `C_required(N) = (f × N) + ops`

   Feasibility condition:

   * `N + C_required(N) <= Deployable`
   * `N + (fN + ops) <= Deployable`
   * `N(1 + f) <= Deployable − ops`
   * `N_collateral_cap = (Deployable − ops) / (1 + f)`

3. **Final max notional:**

   * `N_max = min(N_oi_cap, N_collateral_cap)`

### 8.4 Target Allocation Construction (Deterministic)

Set:

* `TargetPerpShortNotionalUSD = N_max`
* `TargetStockLongUSD = N_max`  *(1x stock; cash-only; no margin)*
* `TargetPerpCollateralUSD = (f × N_max) + ops`
* `TargetCoinbaseTreasuryUSD = Deployable − TargetStockLongUSD − TargetPerpCollateralUSD`

And:

* `CoinbaseTotalUSD = EmergencyBuffer + TargetCoinbaseTreasuryUSD`

### 8.5 Estimated Return (Net APR; explicit)

Compute:

1. **Funding APR for shorts**
   Positive funding means **longs pay shorts** (short earns). ([Chainstack][12])

   * Use `fundingAPR_ema_3d[t]` as the expected short-side funding APR.

2. **Coinbase APR**

   * `coinbaseAPR = user_input_coinbase_apr` (default 3.50) ([Coinbase Help][5])

3. **Insurance drag APR**

   * `insuranceAnnualBudgetUSD = NAV × (insurance_budget_pct / 100)`
   * `insuranceDragAPR = insuranceAnnualBudgetUSD / NAV`

4. **Fees drag APR (conservative constant)**

   * Use Hyperliquid published fees page as the basis; MVP uses a constant taker-fee assumption (config constant) to avoid tier complexity. ([Hyperliquid Docs][6])

5. **Net APR**

   ```
   estNetAPR =
      (fundingAPR_ema_3d[t] × (TargetPerpShortNotionalUSD / NAV))
    + (coinbaseAPR × (CoinbaseTotalUSD / NAV))
    − insuranceDragAPR
    − estFeeDragAPR
   ```

6. **Net $/day**

   * `estNetUSDPerDay = (estNetAPR / 100) × NAV / 365`

---

## 9) Alerts (Pushover) — Definitive

### 9.1 Notification Transport

Use Pushover API for push notifications. ([Pushover][13])

### 9.2 Severity Levels + Triggers

* **🟢 INFO**

  * When best eligible ticker is within **10 APR points** of the hurdle threshold vs current.
* **🟠 OPPORTUNITY**

  * When hurdle condition is met (Section 7.5), and recommendation is executable (during NYSE core hours), else mark as PENDING and send OPPORTUNITY with “execute at next open.”
* **🔴 CRITICAL**

  * When any hard safety filter fails for the current holding:

    * Public status fails
    * Price divergence > 1.5%
    * Hyperliquid 24h notional volume < $5M
  * When **current vs target drift** exceeds $5,000 on any one action item (Section 10.2), and persists for > 30 minutes.

### 9.3 Emergency Escalation (CRITICAL)

CRITICAL alerts must be sent as **Pushover Emergency (priority=2)** and must include `retry` and `expire` parameters. ([Pushover][13])

### 9.4 Alert Deduplication (Required)

Persist last-sent timestamps in SQLite and enforce:

* OPPORTUNITY: max 1 per ticker per 6 hours unless advantage increases by +10 APR points
* CRITICAL: resend every 15 minutes until user marks “Acknowledged” in UI

---

## 10) “Current State” vs “Target State” and Action Items

### 10.1 Current State Inputs (Manual; persisted)

User enters:

* Coinbase USDC balance
* Brokerage cash balance
* Brokerage stock position (ticker + market value)
* Perp collateral balance (USDC)
* Perp short notional (USD)

### 10.2 Action Item Generation

For each bucket, compute deltas:

* `ΔCoinbase = TargetCoinbaseTotalUSD − CurrentCoinbaseUSD`
* `ΔPerpCollateral = TargetPerpCollateralUSD − CurrentPerpCollateralUSD`
* `ΔStock = TargetStockLongUSD − CurrentStockLongUSD`
* `ΔPerpShort = TargetPerpShortNotionalUSD − CurrentPerpShortNotionalUSD`

Generate checklist items for any `abs(delta) >= 1000` with plain-English commands:

* Transfer between Coinbase and Perp collateral account (manual)
* Buy/sell stock (manual)
* Open/close short perp (manual)

“Rebalance Needed” is true if any action item delta exceeds **$5,000**.

---

## 11) Local Architecture (Runs Continuously)

### 11.1 Two Local Processes (required)

1. **Worker daemon (`arbiter_worker`)**

   * Runs continuously
   * Executes the schedules in Sections 7.1 and 7.2
   * Writes computed outputs + alert events to SQLite
   * Sends Pushover notifications

2. **Streamlit UI (`arbiter_ui`)**

   * Serves the dashboard on the local network
   * Reads latest computed outputs from SQLite
   * Writes user input changes to SQLite

### 11.2 Scheduling Library (required)

Use APScheduler with a persistent job store configuration to ensure scheduled tasks do not duplicate across restarts. ([apscheduler.readthedocs.io][14])

### 11.3 Persistence (required)

Use SQLite database file: `./data/arbiter.sqlite`

Must persist:

* all user inputs
* last computed market snapshots (latest)
* EMA history inputs for last 9 epochs per tracked ticker
* last computed target state
* action items
* alert history + dedupe state
* insurance budget ledger + reminders

---

## 12) UI/UX Spec (Mobile-first Streamlit)

Single scroll page with a sticky header.

### 12.1 Sticky Header: Health + Return

* Status pill: **OPTIMIZED / ACTION / CRITICAL**
* Current target ticker + funding EMA APR
* Estimated Net APR + Estimated $/day

### 12.2 Action Items (Checklist)

Generated items with checkboxes that update the Current State when checked (checkbox implies user executed the step).

### 12.3 Opportunity Scanner (Top Table)

Show top 10 eligible tickers:

* ticker
* funding EMA APR (3d)
* instantaneous funding APR
* 24h volume (HL)
* OI USD
* max recommended notional (by caps)
* “advantage vs current” (APR points)

### 12.4 Allocation Panel

Show Target State and Current State side-by-side:

* Coinbase total (Emergency + Treasury)
* Stock long
* Perp collateral
* Perp short notional
* Perp leverage

---

## 13) Insurance Manager (Budget + “What to Buy”)

### 13.1 Budget

* Annual insurance budget = `NAV × 1.50%`
* Monthly budget = annual / 12

### 13.2 “What to Buy” Recommendation (Product Category)

* For funds held on Coinbase: recommend **Custody-style cover**
* For funds deposited to Hyperliquid/bridge/protocol: recommend **Protocol Cover** (designed for DeFi smart contract / protocol risks). ([nexusmutual.io][15])

### 13.3 Required Disclosure

UI must show: Nexus Mutual cover is **discretionary** and members have final say on claims. ([docs.nexusmutual.io][2])

### 13.4 Coverage Targets (Displayed, not enforced)

* Coinbase target covered amount: `80% × trailing 7-day avg CoinbaseTotalUSD`
* Hyperliquid target covered amount: `100% × trailing 7-day avg PerpCollateralUSD`

### 13.5 Reminders

* ACTION: cover expires within 7 days (user enters expiry dates manually)
* CRITICAL: cover expired

---

## 14) Technical Stack (Definitive)

* **Language:** Python 3.12
* **UI:** Streamlit
* **Worker:** APScheduler
* **DB:** SQLite
* **HTTP:** httpx
* **Data ingestion:**

  * Hyperliquid `/info` (`metaAndAssetCtxs`, `fundingHistory`) ([Hyperliquid Docs][1])
  * Nasdaq symbol directory downloads (public status gate) ([NASDAQ Trader][10])
* **Notifications:** Pushover ([Pushover][13])

---

## 15) Roadmap (Fixed)

### Phase 1 (MVP: 3–5 days)

* Worker + Streamlit skeleton
* SQLite persistence
* Hyperliquid metaAndAssetCtxs ingestion
* Allocation calculator + action items
* Estimated net APR

### Phase 2 (Scanner + Alerts: 3–5 days)

* Funding history EMA (top N=30)
* Eligibility filters
* Pushover alerts + dedupe

### Phase 3 (Insurance + Polish: 2–4 days)

* Insurance budget + product recommendation panel
* Expiry reminders
* UI refinements + mobile ergonomics

---

If you want, paste your preferred constant values (especially `maintenance_buffer_pct`, `perp_ops_reserve_usd`, and whether the **$5M** threshold should be Hyperliquid volume only or also equity volume), and I’ll lock those into the PRD as final v1.2.

[1]: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint?utm_source=chatgpt.com "Info endpoint | Hyperliquid Docs - GitBook"
[2]: https://docs.nexusmutual.io/resources/faq/?utm_source=chatgpt.com "FAQ | Nexus Mutual Documentation"
[3]: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding?utm_source=chatgpt.com "Funding - Hyperliquid Docs - GitBook"
[4]: https://docs.chainstack.com/reference/hyperliquid-info-meta-and-asset-ctxs?utm_source=chatgpt.com "metaAndAssetCtxs | Hyperliquid info"
[5]: https://help.coinbase.com/en/coinbase/coinbase-staking/rewards/usd-coin-rewards-faq?utm_source=chatgpt.com "USDC rewards overview"
[6]: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees?utm_source=chatgpt.com "Fees | Hyperliquid Docs"
[7]: https://www.quicknode.com/docs/hyperliquid/info-endpoints/fundingHistory?utm_source=chatgpt.com "fundingHistory Info Endpoint Method | Hyperliquid Docs"
[8]: https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt?utm_source=chatgpt.com "nasdaqlisted.txt"
[9]: https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt?utm_source=chatgpt.com "otherlisted.txt"
[10]: https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs&utm_source=chatgpt.com "Symbol Look-Up/Directory Data Fields & Definitions"
[11]: https://www.nyse.com/markets/hours-calendars?utm_source=chatgpt.com "Holidays & Trading Hours"
[12]: https://docs.chainstack.com/reference/hyperliquid-info-user-funding?utm_source=chatgpt.com "userFunding | Hyperliquid info"
[13]: https://pushover.net/api?utm_source=chatgpt.com "Pushover: API"
[14]: https://apscheduler.readthedocs.io/en/3.x/userguide.html?utm_source=chatgpt.com "User guide — APScheduler 3.11.2.post1 documentation"
[15]: https://nexusmutual.io/blog/bundled-protocol-cover-is-live?utm_source=chatgpt.com "Nexus Mutual Launches Bundled Protocol Cover to Protect ..."
