# Architecture

This document explains how SAMPADA.ai is built, how data flows through it, and the methodology behind its analytics.

---

## 1. System overview

SAMPADA.ai is a three-tier system:

1. **React SPA (Vercel)** — the user interface. Talks to Firebase directly for auth/watchlist and to the FastAPI backend for analysis.
2. **FastAPI backend (Render)** — the analytical engine. Aggregates market data, runs valuation models, builds PowerPoint decks, and verifies auth tokens.
3. **Firebase (Google Cloud)** — Email/Password authentication and a Firestore document store for user profiles, watchlists, and saved analysis sessions.

```
User ──▶ React SPA ──┬──▶ FastAPI  ──▶ yfinance / yahooquery / finviz / Alpha Vantage
                     │       │
                     │       └──▶ Firebase Admin SDK (verify token, save session)
                     │
                     └──▶ Firebase Web SDK (auth, watchlist read/write)
```

### Why split auth between client and server?
- The **client** uses the Web SDK so the watchlist is realtime (`onSnapshot`) and writes don't need a backend round-trip.
- The **server** uses the Admin SDK to verify ID tokens (so it can trust who's calling) and to write session history with elevated privileges.
- Firestore **security rules** ([firestore.rules](../firestore.rules)) restrict every client read/write to the caller's own UID; the Admin SDK bypasses rules, so server writes are unaffected.

---

## 2. Backend modules

| Module | Responsibility |
|--------|----------------|
| [`main.py`](../backend/main.py) | FastAPI app, CORS, ticker validation, all routes, startup diagnostic, and the 8-slide PowerPoint engine. |
| [`analysis.py`](../backend/analysis.py) | The analytical core: WACC/DCF, peer discovery & scoring, the news pipeline, analyst consensus, quality scores, SWOT, confidence scoring. |
| [`services.py`](../backend/services.py) | `AlphaVantageService` (macro + commodities via yfinance futures) and `FinvizService` (fundamentals scrape + peer screener). |
| [`auth.py`](../backend/auth.py) | Firebase Admin SDK lifecycle: credential loading (JSON string or file path), startup handshake, token verification, session persistence. |
| [`utils.py`](../backend/utils.py) | Ticker resolution, currency symbols, large-number formatting, FX rates, data normalization, PPT footer. |
| [`models.py`](../backend/models.py) | Pydantic models (`PeerData`, `ExportRequest`). |
| [`config.py`](../backend/config.py) | API keys, cache settings, the global macro map (risk-free tickers + ERP per currency), and the legal disclaimer. `.env` is auto-loaded. |

### Request lifecycle for `GET /api/analyze/{ticker}`

1. **Validate & resolve** the ticker (regex guard, dynamic resolution).
2. **Cache check** — return immediately if a result younger than `CACHE_DURATION` (900 s) exists.
3. **Fetch core data** — `_fetch_core_quote` retries yfinance `info` with backoff, then falls back to **yahooquery** (see §9) so a throttled Yahoo IP doesn't hard-404; plus macro + commodities.
4. **DCF** — `run_institutional_dcf` (dual WACC, normalized FCF, 10-yr projection).
5. **Peers** — `get_robust_peers` produces Category A/B candidate lists; each is normalized via `process_peer_data` and scored by `calculate_peer_distance`. Top 4 A + 2 B are kept.
6. **Comps valuation** — similarity-weighted harmonic mean of P/E and EV/EBITDA.
7. **Blend** — `final = 0.6·DCF + 0.4·comps`; `verdict` derived from upside.
8. **News + sentiment**, **theses**, **financials**, **historical range**, **consensus**, **quality scores**, **SWOT**, **confidence**.
9. **Assemble** the response, write to cache.
10. If an `Authorization` bearer token is present and valid, **save the session** to Firestore (best-effort, never blocks the response).

---

## 3. Valuation methodology

### DCF (intrinsic value)
- **Beta** — Blume-adjusted (`0.67·raw + 0.33·1.0`), capped per scenario.
- **WACC** — computed twice: a conservative **base** and a **stress** case. Cost of equity via CAPM (`rf + β·ERP`); cost of debt from interest expense / total debt (floored/capped); weighted by market cap vs. debt. Bounded to sane ranges.
- **Growth** — ROIC-derived (`ROE·(1−payout)`), bounded 5–25%, fading to a terminal rate.
- **FCF normalization (sector-adjusted)** — the lookback window is **dynamic**: 7 years for highly cyclical sectors (Energy, Basic Materials) to smooth the business cycle, down to 3 years for asset-light/growth firms (Tech, Consumer Defensive) to prioritise recency (`_fcf_lookback_years`). **CAPEX is smoothed** (mean CapEx/Revenue applied to latest revenue, removing one-off spikes) and **NWC is normalized** (the year's working-capital swing is swapped for the through-cycle mean so a single inventory/receivables move doesn't distort the run-rate). The result is blended 60/40 with reported FCF so one noisy statement can't dominate.
- **Output** — a base value plus a low/high range from the scenario spread.

### Comparables (relative value)
- Peers scored by similarity across growth, margin, ROIC, capex intensity, and net-debt/EBITDA.
- Multiples aggregated with a **similarity-weighted harmonic mean** (harmonic mean is the correct average for ratios; weighting tilts toward the closest comparables).
- Implied price derived from the target's own metric × the peer multiple.

### Dynamic blend & verdict
The fixed 60/40 split is replaced by a **Bayesian-inspired dynamic weighting** (`calculate_blend_weights`). A "DCF reliability" prior in [0,1] is built from sector cyclicality, CAPEX intensity, beta, and profitability, then mapped to a DCF weight in **[0.30, 0.80]**:

```
w_dcf   ∈ [0.30, 0.80]   (asset-light, predictable, low-beta ⇒ higher)
w_comps = 1 − w_dcf      (cyclical, capital-intensive, high-beta ⇒ higher)
final_value = w_dcf · DCF + w_comps · comps
verdict     = POSITIVE BIAS (upside > 15%) | NEGATIVE BIAS (< −10%) | NEUTRAL
```

The chosen weights and FCF lookback window are returned in `valuation_analysis` and shown in the UI.

### Confidence score
Combines peer count/quality, DCF validity, data completeness, and event risk into a single 0–100 robustness indicator.

---

## 4. Peer engine (formalized cascade)

Candidate symbols are sourced (Finviz screener for US + yahooquery recommendations), their metadata fetched **once** in a batch, then run through a rigid filter cascade (`get_robust_peers`):

| Filter | Rule |
|--------|------|
| 1 | GICS **Sector + Industry** match (always required for Category A) |
| 2 | **Revenue** within ±30% of target |
| 3 | **Market cap** within ±50% of target |
| 4 | **ROIC + EBITDA-margin** proximity (+ log market-cap distance) for ranking |

If the strictest tier yields fewer than 3 names, the size bands are **relaxed one tier at a time** (±50% cap → 0.1–10× cap → size-unconstrained), and the tier actually used is reported back in `peer_methodology.tier_used` and surfaced in the peers-modal UI so reviewers see exactly how the cohort was generated.

- **Category A** — direct comparables (same sector + industry), ranked by combined size/ROIC/margin proximity (shown in cyan).
- **Category B** — same sector, different industry, sized 0.2–5× (scale benchmarks, shown in amber).
- **Normalization** — every peer's price and market cap are converted into the target's currency via live FX before multiples are computed.

> Mega-cap outliers (e.g. a $3T company in a thin industry) may legitimately fall through to the size-unconstrained tier — the UI labels this honestly rather than fabricating size-matched peers.

---

## 5. News pipeline (four-tier fallback)

Implemented in `get_smart_news`. Tiers run until enough headlines are collected, guaranteeing the UI is never empty:

1. **yfinance structured news** — normalized through `_normalize_yf_news`, which handles **both** the legacy flat schema and the newer nested `{'content': {...}}` schema (the latter previously caused silent empty feeds).
2. **Finviz scrape** (US listings).
3. **Yahoo Finance RSS** + **Google News RSS** with a broadened, finance-weighted query.
4. **Sector / industry macro news** — final fallback; the response is tagged `fallback: "sector"` so the UI can show a "broader sector news" note.

Each headline is scored by the **NLP engine** (see §6.5), then weighted by source credibility, relevance, and **time decay** (3-day half-life). Aggregated into short-term (3d), medium-term (30d), and an event-risk flag. Titles are batch-scored in a single call to bound latency.

### 6.5 Sentiment engine (FinBERT + fallback)

[`nlp.py`](../backend/nlp.py) provides `batch_sentiment(titles)`:

- **Primary** — `ProsusAI/finbert` via the Hugging Face Inference API (activated when `HF_API_TOKEN` is set). FinBERT is trained on financial text, so it reads jargon and context — e.g. *"margins compressed due to temporary inventory actions"* is not blindly scored negative. Compound score = `P(positive) − P(negative)`.
- **Fallback** — an enhanced VADER lexicon (finance keyword nudges) used whenever the token is unset, the model is cold-loading (HTTP 503), the request times out, or anything errors.
- **Bounded** — single batched request per analysis, `HF_TIMEOUT`-second cap, results cached. We deliberately do **not** load transformers/torch locally — that would exceed Render's 512 MB free tier.

The active engine name is returned in `sentiment_analysis.nlp_engine` and shown in the UI.

---

## 6. Analyst consensus

A multi-source cascade so coverage is shown whenever it exists anywhere:

1. yfinance `info` — `targetMeanPrice`, `recommendationKey`, `recommendationMean`.
2. yfinance `upgrades_downgrades` — grade counts over the trailing 12 months (tz-normalized DatetimeIndex handling).
3. yahooquery `recommendation_trend` — aggregated buy/hold/sell.
4. yahooquery `financial_data` — recommendation + target fallback.

The `recommendation_mean` (1.0 = Strong Buy … 5.0 = Strong Sell) drives the SVG gauge in the UI. When nothing is found, `data_available: false` triggers the "Coverage Paused" state.

---

## 6.6 Model validation / backtesting

[`run_backtest`](../backend/analysis.py) quantifies how well the fair-value estimate tracks reality, returned in `backtest` and rendered as a "Model Validation — Historical Accuracy" card.

**What it computes** (from real trailing-12-month monthly closes):
- `mape` — mean absolute % distance between the fair value and the realized price path.
- `consensus_mape` — the same metric for the analyst mean target.
- `outperformance` — `consensus_mape − mape` (positive ⇒ the model tracked closer than the street).
- `hit_ratio` — % of months the close fell inside the DCF implied range.
- `converged`, `actual_return` — directional/contextual color.

**Honest scoping** — this is a **proximity/convergence** validation, *not* a look-ahead-free point-in-time backtest. Free data sources don't expose the historical fundamentals needed to recompute the model at each past date, so we measure how closely the **current** fair value sits to where the stock **actually** traded. The methodology string is shipped alongside the numbers, and the UI states it verbatim — no accuracy figure is hardcoded; everything is computed from live price history.

## 7. Macro & commodities

- **Commodities** — yfinance futures (`CL=F` WTI, `BZ=F` Brent, `GC=F` Gold, `NG=F` Nat Gas, `ALI=F` Aluminum). Free, keyless.
- **Risk-free rate** — yfinance `^TNX` (10Y Treasury), with Alpha Vantage `TREASURY_YIELD` as fallback.
- **Inflation** — Alpha Vantage CPI (best-effort; non-critical).

---

## 8. PowerPoint export engine

`POST /api/export_ppt` builds an 8-slide deck with `python-pptx` using an explicit dark navy/cyan palette (no default layouts):

1. Cover (verdict badge, KPI row, summary excerpt)
2. Disclaimer
3. Financial tear sheet
4. Visual football field (range bars + current-price marker; stress strip in Internal mode)
5. Bull/Bear narrative (Internal) or Investment Highlights (Client)
6. Comparable analysis table (Category A/B color-coded)
7. SWOT 2×2 (Internal) or Risk Disclosures (Client)
8. WACC bridge + model detail (Internal) or Closing (Client)

**View modes** — `Internal` exposes stress scenarios, SWOT, and the WACC bridge; `Client` sanitizes negative verdicts to "REVIEW" and hides internal-only analytics.

---

## 9. Resilience & security

- **Caching** — 15-minute in-memory cache keyed by resolved ticker.
- **Anti-rate-limit** — rotating user-agent pool + exponential backoff with jitter (`_with_retry`) on all external calls.
- **Multi-source quote failover** — `_fetch_core_quote` retries yfinance `info` with backoff and, if Yahoo throttles the host IP (common on shared cloud egress like Render's free tier), falls back to **yahooquery** (`_yahooquery_info_fallback`) to assemble a yfinance-`info`-compatible dict. The analysis degrades gracefully (peers/financials may thin out) instead of returning a hard 404. The same anti-throttle reasoning is why the news pipeline spans four independent sources.
- **CORS** — locked to `ALLOWED_ORIGINS`; not a wildcard.
- **Input validation** — strict regex on all ticker/query inputs before they touch any data provider.
- **Error hiding** — a global exception handler logs internally but returns a generic message; OpenAPI docs are disabled in production.
- **Secrets** — the Firebase service account is an env var, never committed. It accepts **base64-encoded JSON** (recommended — immune to env-injector newline/quote mangling) or raw JSON, with `private_key` newline repair (`_parse_service_account`). The root [`.gitignore`](../.gitignore) blocks credential files, `.env`, `venv/`, and `node_modules/`.
- **Firestore rules** — per-user lockdown (`request.auth.uid == userId`), default-deny everywhere else.

---

## 10. Frontend structure

| File | Role |
|------|------|
| [`App.js`](../frontend/src/App.js) | Auth gate, analysis flow, dashboard grid, all modals (tear sheet, methods, news, peers), watchlist/profile wiring, PPT export. |
| [`components.js`](../frontend/src/components.js) | Reusable UI: `ConsensusGauge`/`ConsensusBar`, `MarketDataCard`, `FootballField`, `HeartButton`, `ProfileModal`, methodology tooltips. |
| [`watchlist.js`](../frontend/src/watchlist.js) | Firestore helpers: add/remove/check/subscribe for `users/{uid}/watchlist`. |
| [`AuthPage.js`](../frontend/src/AuthPage.js) | Login/signup, guest-mode fallback, friendly Firebase error messages. |
| [`firebase.js`](../frontend/src/firebase.js) | Firebase app/auth/firestore initialization from env vars. |
| [`App.css`](../frontend/src/App.css) | The cinematic dark theme, grid layout, modals, custom scrollbars. |

The app gates on `onAuthStateChanged`: `undefined` → loading, `null` → `AuthPage`, otherwise the dashboard. Guest mode (`user.isGuest`) disables auth-dependent features but keeps analysis fully functional.
