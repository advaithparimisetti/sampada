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
3. **Fetch core data** — yfinance `info` + price; macro + commodities.
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
- **FCF** — normalized across up to 3 years of revenue-margin history to dampen one-off distortions.
- **Output** — a base value plus a low/high range from the scenario spread.

### Comparables (relative value)
- Peers scored by similarity across growth, margin, ROIC, capex intensity, and net-debt/EBITDA.
- Multiples aggregated with a **similarity-weighted harmonic mean** (harmonic mean is the correct average for ratios; weighting tilts toward the closest comparables).
- Implied price derived from the target's own metric × the peer multiple.

### Blend & verdict
```
final_value = 0.6 · DCF + 0.4 · comps
upside      = (final_value − price) / price
verdict     = POSITIVE BIAS (upside > 15%) | NEGATIVE BIAS (< −10%) | NEUTRAL
```

### Confidence score
Combines peer count/quality, DCF validity, data completeness, and event risk into a single 0–100 robustness indicator.

---

## 4. Peer engine (Category A / B)

- **Category A** — same sector **and** industry: true direct comparables (shown in cyan).
- **Category B** — same sector, different industry: scale/size benchmarks (shown in amber).
- **Discovery sources** — Finviz screener (US, industry-filtered) → yahooquery → yfinance, with promotion of B→A when A is empty (important for thin international coverage).
- **Normalization** — every peer's price and market cap are converted into the target's currency via live FX before multiples are computed.

---

## 5. News pipeline (four-tier fallback)

Implemented in `get_smart_news`. Tiers run until enough headlines are collected, guaranteeing the UI is never empty:

1. **yfinance structured news** — normalized through `_normalize_yf_news`, which handles **both** the legacy flat schema and the newer nested `{'content': {...}}` schema (the latter previously caused silent empty feeds).
2. **Finviz scrape** (US listings).
3. **Yahoo Finance RSS** + **Google News RSS** with a broadened, finance-weighted query.
4. **Sector / industry macro news** — final fallback; the response is tagged `fallback: "sector"` so the UI can show a "broader sector news" note.

Each headline is scored with **VADER** sentiment, a directional keyword adjustment, a source-credibility weight, a relevance weight, and **time decay** (3-day half-life). Aggregated into short-term (3d), medium-term (30d), and an event-risk flag.

---

## 6. Analyst consensus

A multi-source cascade so coverage is shown whenever it exists anywhere:

1. yfinance `info` — `targetMeanPrice`, `recommendationKey`, `recommendationMean`.
2. yfinance `upgrades_downgrades` — grade counts over the trailing 12 months (tz-normalized DatetimeIndex handling).
3. yahooquery `recommendation_trend` — aggregated buy/hold/sell.
4. yahooquery `financial_data` — recommendation + target fallback.

The `recommendation_mean` (1.0 = Strong Buy … 5.0 = Strong Sell) drives the SVG gauge in the UI. When nothing is found, `data_available: false` triggers the "Coverage Paused" state.

---

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
- **CORS** — locked to `ALLOWED_ORIGINS`; not a wildcard.
- **Input validation** — strict regex on all ticker/query inputs before they touch any data provider.
- **Error hiding** — a global exception handler logs internally but returns a generic message; OpenAPI docs are disabled in production.
- **Secrets** — the Firebase service account is provided as an env var (JSON string on Render), never committed. The root [`.gitignore`](../.gitignore) blocks credential files, `.env`, `venv/`, and `node_modules/`.
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
