# SAMPADA

**Institutional-Style Equity Intelligence** — a full-stack equity research platform that turns a single ticker into a banker-grade valuation workspace: dual-engine valuation (DCF + comparables), peer benchmarking, sentiment-weighted news, analyst consensus, a live football field, and one-click PowerPoint export.

> ⚠️ **Educational simulation only.** SAMPADA.ai produces computer-generated analytical estimates for learning purposes. It is **not** investment advice, a recommendation, or an offer to buy or sell securities.

---

## Table of Contents

- [Features](#features)
- [Architecture at a glance](#architecture-at-a-glance)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [Environment variables](#environment-variables)
- [Deployment](#deployment)
- [Further documentation](#further-documentation)

---

## Features

| Area | What it does |
|------|--------------|
| **Dual-engine valuation** | Institutional DCF (Blume-adjusted beta, dual base/stress WACC, 10-year projection) with **sector-adjusted FCF normalization** (3–7yr lookback by cyclicality, CAPEX smoothing, NWC normalization), **dynamically blended** with comparables via a Bayesian-inspired weighting (asset-light/predictable ⇒ DCF-heavy; cyclical/capital-intensive ⇒ Comps-heavy). |
| **Formalized peer engine** | Rigid cascading selection: GICS sector+industry → revenue ±30% → market cap ±50% → ROIC/margin ranking, with documented relaxation tiers. The exact methodology used is surfaced in the UI. Category A (direct) / Category B (scale) split. |
| **FinBERT NLP** | Financial-domain sentiment via `ProsusAI/finbert` (HF Inference API) that reads jargon and context, with an enhanced-VADER fallback. Batched and latency-bounded. |
| **Model validation** | A backtesting module computing real MAPE, hit-ratio, and out/under-performance vs analyst consensus over the realized 12-month price path. |
| **Valuation football field** | Visual range bars for 52-week, analyst targets, comps, and DCF, with a live current-price marker. |
| **Resilient news pipeline** | Four-tier fallback (yfinance → Finviz → Yahoo/Google RSS → sector macro) with FinBERT/VADER sentiment + 3-day half-life time decay, so the wire is never empty. |
| **Analyst consensus** | Multi-source cascade producing a 1–5 `recommendationMean` gauge, buy/hold/sell counts, and mean/high/low price targets. |
| **Macro & commodities** | Live WTI, Brent, Gold, Natural Gas (yfinance futures) + 10Y Treasury yield and CPI inflation. |
| **Auth & watchlist** | Firebase Email/Password auth; per-user Cloud Firestore watchlist with a persistent ♥ toggle and a profile modal. |
| **PowerPoint export** | 8-slide investment-banking-grade deck (cover, disclaimer, tear sheet, football field, bull/bear, comps, SWOT, WACC bridge) with **Internal** and **Client** view modes. |
| **Live ticker search** | Keyless debounced autocomplete via `yahooquery.search()`. |

---

## Architecture at a glance

```
┌─────────────────────┐         HTTPS / JSON          ┌──────────────────────┐
│   React 19 SPA       │  ───────────────────────────▶ │   FastAPI backend     │
│   (Vercel)           │   /api/analyze, /export_ppt   │   (Render, uvicorn)   │
│                      │ ◀───────────────────────────  │                       │
│  Firebase Web SDK    │                               │  yfinance / yahooquery│
└─────────┬────────────┘                               │  finviz / Alpha Vant. │
          │                                            │  python-pptx          │
          │ Auth + Firestore (direct)                  └──────────┬────────────┘
          ▼                                                       │ Admin SDK
┌──────────────────────────────────────────────────────────────────────────┐
│                         Firebase (Google Cloud)                            │
│   Authentication (Email/Password)   ·   Firestore (users/{uid}/…)          │
└────────────────────────────────────────────────────────────────────────────┘
```

- The **frontend** talks to Firebase directly (Web SDK) for auth and watchlist reads/writes, and to the **backend** for all analysis.
- The **backend** verifies Firebase ID tokens with the Admin SDK and persists analysis sessions to Firestore. It bypasses security rules (privileged), so server writes are unaffected by client rules.
- All market data flows through the backend, which caches responses for 15 minutes and applies anti-rate-limit measures (rotating user-agents, exponential backoff).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the deep dive.

---

## Tech stack

**Frontend** — React 19, Create React App, Axios, Recharts, Firebase Web SDK v12.
**Backend** — Python 3.11+, FastAPI, Uvicorn, yfinance, yahooquery, finvizfinance, python-pptx, vaderSentiment, firebase-admin, pandas/numpy.
**Infra** — Vercel (frontend), Render (backend), Firebase Auth + Firestore.

---

## Repository layout

```
sampada/
├── README.md                  ← you are here
├── render.yaml                ← Render deploy config (backend)
├── firestore.rules            ← Firestore security rules
├── docs/
│   ├── ARCHITECTURE.md        ← system & module deep dive
│   ├── API.md                 ← REST endpoint reference
│   └── DEPLOYMENT.md          ← step-by-step production deploy
├── backend/
│   ├── main.py                ← FastAPI app, routes, PPT engine
│   ├── analysis.py            ← valuation, peers, news, consensus
│   ├── services.py            ← commodities/macro + Finviz services
│   ├── auth.py                ← Firebase Admin SDK integration
│   ├── utils.py               ← ticker/currency/formatting helpers
│   ├── models.py              ← Pydantic request/response models
│   ├── config.py              ← keys, cache, macro map, disclaimer
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── App.js             ← dashboard, modals, analysis flow
    │   ├── components.js      ← reusable UI (gauge, heart, profile, cards)
    │   ├── watchlist.js       ← Firestore watchlist helpers
    │   ├── AuthPage.js        ← login / signup / guest mode
    │   ├── firebase.js        ← Firebase initialization
    │   └── App.css            ← cinematic dark theme
    ├── .env.example
    └── package.json
```

---

## Local development

### Prerequisites
- **Node.js** 18+ and npm
- **Python** 3.11+
- A **Firebase** project with Email/Password auth and Firestore enabled (optional — the app falls back to guest mode without it)

### 1. Backend

```bash
cd backend
python -m venv venv
# Windows:  venv\Scripts\activate     macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt

# Create backend/.env (see Environment variables below), then:
uvicorn main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (health check at `/`, diagnostics at `/api/diagnostics`).

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local     # fill in Firebase values; leave REACT_APP_API_URL blank for local
npm start
```

The app opens at `http://localhost:3000` and proxies analysis calls to `http://localhost:8000`.

> Without Firebase config the frontend runs in **guest mode** — analysis works, but auth, watchlist, and session history are disabled.

---

## Environment variables

### Backend (`backend/.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | prod | Full service-account JSON **string** (used on Render). |
| `FIREBASE_SERVICE_ACCOUNT` | local | Path to the service-account `.json` file (local dev alternative). |
| `ALLOWED_ORIGINS` | prod | Comma-separated CORS allowlist, e.g. `https://yourapp.vercel.app`. Defaults to localhost. |
| `ALPHA_VANTAGE_KEY` | optional | Only used for the CPI inflation fallback. |
| `HF_API_TOKEN` | optional | Hugging Face token to activate FinBERT sentiment via the Inference API. Without it, the NLP engine falls back to an enhanced VADER lexicon. |
| `HF_TIMEOUT` | optional | FinBERT request timeout in seconds (default `8`). Bounds news-pipeline latency. |

> `FIREBASE_SERVICE_ACCOUNT_JSON` accepts **either** raw JSON **or** base64-encoded JSON. On Render, base64 is recommended — it avoids the newline/quote-escaping issues that otherwise corrupt the service-account `private_key`.

### Frontend (`frontend/.env.local`)

| Variable | Purpose |
|----------|---------|
| `REACT_APP_API_URL` | Backend base URL. Blank → `http://localhost:8000`. |
| `REACT_APP_FIREBASE_API_KEY` | Firebase web config. |
| `REACT_APP_FIREBASE_AUTH_DOMAIN` | Firebase web config. |
| `REACT_APP_FIREBASE_PROJECT_ID` | Firebase web config. |
| `REACT_APP_FIREBASE_STORAGE_BUCKET` | Firebase web config. |
| `REACT_APP_FIREBASE_MESSAGING_SENDER_ID` | Firebase web config. |
| `REACT_APP_FIREBASE_APP_ID` | Firebase web config. |

> Firebase Web config values are **not secrets** — they are safe to expose in the client bundle. The service-account JSON **is** a secret and must only live in backend env vars, never in git.

---

## Deployment

Production runs on **Vercel** (frontend) + **Render** (backend) + **Firebase** (auth/data). The short version:

1. Push to GitHub.
2. Render → New Web Service, root `backend`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`; set `FIREBASE_SERVICE_ACCOUNT_JSON`, `ALLOWED_ORIGINS`.
3. Vercel → New Project, root `frontend`; set `REACT_APP_API_URL` + Firebase vars.
4. Add your Vercel domain to `ALLOWED_ORIGINS` (Render) **and** Firebase → Authentication → Authorized domains.
5. Deploy the Firestore rules: `firebase deploy --only firestore:rules` (or paste [firestore.rules](firestore.rules) into the console).

Full walkthrough with troubleshooting in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Further documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — module-by-module breakdown, data flow, valuation methodology, caching & rate-limit strategy.
- **[docs/API.md](docs/API.md)** — every REST endpoint with parameters and response shapes.
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — production deployment, env setup, and common errors.

---

## License & disclaimer

Educational project. All valuations are simulated estimates and must not be used for real investment decisions. Market data is sourced from third-party providers (Yahoo Finance, Finviz, Alpha Vantage) subject to their respective terms.
