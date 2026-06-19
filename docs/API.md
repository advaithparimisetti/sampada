# API Reference

Base URL (local): `http://localhost:8000`
Base URL (prod): your Render service URL, e.g. `https://sampada-xxxx.onrender.com`

All responses are JSON unless noted. CORS is restricted to the origins in `ALLOWED_ORIGINS`. Internal errors return a generic `{"detail": "An internal error occurred."}` (stack traces are never leaked). OpenAPI docs (`/docs`, `/redoc`) are disabled in production.

---

## Health & diagnostics

### `GET /`
Liveness probe (used by Render's health check).
```json
{ "status": "ok", "app": "SAMPADA.ai" }
```

### `GET /api/diagnostics`
Runtime status, including the Firebase startup handshake result.
```json
{
  "app": "SAMPADA.ai",
  "version": "2.1.0",
  "firebase": { "status": "ok|partial|disabled|error", "uid_test": "auth_ok", "firestore": true, "error": null },
  "cache_entries": 3
}
```

---

## Authentication

### `POST /api/auth/verify`
Verifies a Firebase ID token.

**Body**
```json
{ "id_token": "<firebase-id-token>" }
```
**Response `200`**
```json
{ "uid": "abc123", "email": "user@example.com", "valid": true }
```
**Errors** — `401` if the token is invalid or Firebase is not configured.

---

## Analysis

### `GET /api/analyze/{ticker}`
The primary endpoint. Runs the full valuation, peer, news, consensus, and financials pipeline. Results are cached server-side for 15 minutes.

**Path params**
- `ticker` — validated against `^[A-Za-z0-9]([A-Za-z0-9.\-]{0,14}[A-Za-z0-9])?$` (max 20 chars). Supports international suffixes (`.NS`, `.L`, `.PA`, etc.). Invalid input → `400`.

**Headers (optional)**
- `Authorization: Bearer <firebase-id-token>` — when present and valid, the analysis is saved to `users/{uid}/sessions`.

**Errors** — `404` if the ticker is unknown or has no market data.

**Response `200` (abridged)**
```jsonc
{
  "symbol": "NVDA",
  "name": "NVIDIA Corporation",
  "currency_symbol": "$",
  "price": 120.34,
  "market_cap": "2.95T",
  "verdict": "POSITIVE BIAS",            // POSITIVE BIAS | NEUTRAL | NEGATIVE BIAS
  "confidence_score": 78,
  "sentiment_score": 61.2,
  "sentiment_analysis": {
    "score": 61.2, "short_term": 61.2, "medium_term": 55.0,
    "event_risk": "Medium",              // Low | Medium | High
    "fallback": "direct",                // direct | sector | none
    "nlp_engine": "FinBERT (HF Inference API)"  // or "VADER (lexicon fallback)"
  },
  "headlines": [
    { "title": "...", "link": "...", "publisher": "Reuters",
      "timestamp": "...", "raw_sentiment": 0.4, "impact_score": 0.31, "is_relevant": true }
  ],
  "consensus": {
    "buy": 38, "hold": 5, "sell": 1,
    "recommendation": "strong_buy",
    "recommendation_mean": 1.6,          // 1=Strong Buy … 5=Strong Sell
    "target_mean": 145.0, "target_high": 200.0, "target_low": 100.0,
    "num_analysts": 44, "data_available": true
  },
  "peers": [
    { "symbol": "AMD", "price": 158.2, "mkt_cap": "255B", "pe": 45.1,
      "ev_ebitda": 30.2, "ev_sales": 8.1, "net_debt_ebitda": 0.4,
      "roic": "12.3%", "similarity": 82.0, "category": "A" }
  ],
  "target_ratios": { "pe": 55.2, "ev_ebitda": 40.1, "ev_sales": 18.0,
                     "net_debt_ebitda": -0.2, "roic": "31.0%", "growth": 22.0 },
  "valuation_analysis": {
    "implied_price": 138.5, "upside": 15.1, "dcf_price": 142.0,
    "dcf_range": "110.0 - 175.0", "comps_price": 132.0,
    "wacc": 0.095, "growth_assumed": 0.18,
    "fcf_lookback": 3,                   // sector-adjusted FCF window (years)
    "blend_dcf": 77, "blend_comps": 23   // dynamic blend weights (%)
  },
  "peer_methodology": {
    "filters": ["GICS Sector & Industry match", "Revenue band ±30% of target",
                "Market cap ±50% of target", "ROIC & margin similarity (ranking)"],
    "tier_used": "Sector+Industry, MktCap ±50%",
    "candidates_screened": 14, "cat_a_count": 4, "cat_b_count": 2
  },
  "backtest": {
    "data_available": true, "window_months": 13,
    "mape": 8.4, "consensus_mape": 9.5, "outperformance": 1.1,
    "hit_ratio": 61.5, "converged": true, "actual_return": 22.0,
    "methodology": "Proximity of fair value to realized 12-month monthly close path …"
  },
  "football_field": {
    "fifty_two_week": [80.0, 140.0], "analyst_target": [100.0, 200.0],
    "dcf_range": [110.0, 175.0], "comps_range": [112.2, 151.8]
  },
  "quality_scores": [ { "subject": "Profitability", "A": 90 }, ... ],
  "swot": { "strengths": [...], "weaknesses": [...], "opportunities": [...], "threats": [...] },
  "financials": { "income": [...], "balance": [...], "cashflow": [...] },
  "tear_sheet_data": { "revenue": "60.9B", "ebitda": "...", ... },
  "wacc_components": { "rf": 0.042, "beta": 1.7, "erp": 0.055, "wacc": 0.095 },
  "market_data": {
    "commodities": { "WTI": 78.3, "BRENT": 82.1, "GOLD": 2330.5, "NATURAL_GAS": 2.1, "ALUMINUM": "N/A" },
    "macro": { "risk_free_rate": 0.042, "inflation": 0.031 }
  },
  "theses": { "bull": "...", "bear": "..." },
  "summary": "NVIDIA Corporation provides graphics...",
  "sector": "Technology", "industry": "Semiconductors"
}
```

---

## Peers & search

### `GET /api/peer_info`
Fetches normalized data for a single peer ticker (used when manually adding a peer).

**Query params**
- `ticker` — validated ticker.
- `base_currency` — currency to normalize multiples into (default `USD`).

**Response** — a single peer object (same shape as an entry in `peers[]`). `404` if not found.

### `GET /api/ticker_search`
Keyless live autocomplete via `yahooquery.search()`.

**Query params**
- `q` — 1–40 chars, `^[A-Za-z0-9 .\-]{1,40}$`. Invalid → `400`.

**Response `200`**
```json
{ "results": [
  { "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NMS", "type": "EQUITY" }
] }
```
Returns up to 8 results filtered to `EQUITY`, `ETF`, `FUND`. Never raises — returns `{ "results": [] }` on failure.

---

## Export

### `POST /api/export_ppt`
Generates an 8-slide PowerPoint deck and streams it back as a `.pptx` file.

**Body** — an `ExportRequest` (see [models.py](../backend/models.py)). Key fields:

| Field | Type | Notes |
|-------|------|-------|
| `ticker`, `target_name`, `verdict` | string | |
| `implied_price`, `current_price`, `upside` | number | |
| `view_mode` | `"Internal"` \| `"Client"` | Internal includes stress scenarios, SWOT, WACC bridge; Client is sanitized. |
| `deal_type` | `"IPO"` \| `"M&A"` \| `"LBO"` | Drives which multiple is emphasized. |
| `peers` | `PeerData[]` | Each tagged category `A` or `B`. |
| `football_field`, `theses`, `swot`, `wacc_components`, `tear_sheet_data`, `market_data`, `valuation_analysis`, `financials` | object | Carried from the analyze response. |

**Response** — `200` with `Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation` (binary blob). `500` if `python-pptx` is unavailable.

---

## Notes on data sources

- **Quotes / fundamentals / financials** — yfinance (primary), yahooquery (secondary).
- **Peers** — Finviz screener (US), yahooquery, yfinance.
- **Commodities** — yfinance futures (`CL=F`, `BZ=F`, `GC=F`, `NG=F`, `ALI=F`).
- **Risk-free rate** — yfinance `^TNX`; Alpha Vantage fallback.
- **Inflation (CPI)** — Alpha Vantage (best-effort).
- **News** — yfinance → Finviz → Yahoo RSS → Google News RSS → sector macro.
