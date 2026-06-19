# backend/analysis.py
import yfinance as yf
import requests
import numpy as np
import math
import random
import time
from datetime import datetime
import urllib.parse
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from yahooquery import Ticker as YQTicker

from config import GLOBAL_MACRO, MAJOR_SOURCES
from utils import normalize_data, get_exchange_rate, format_large_number
from services import FinvizService
from nlp import batch_sentiment as nlp_batch_sentiment, engine_name as nlp_engine_name

# ---------------------------------------------------------------------------
# ANTI-RATE-LIMIT PRIMITIVES
# ---------------------------------------------------------------------------
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1",
]


def _rand_ua() -> dict:
    return {"User-Agent": random.choice(_UA_POOL)}


def _with_retry(fn, retries=3, base_delay=1.0):
    for attempt in range(retries):
        try:
            result = fn()
            return result
        except Exception as exc:
            if attempt == retries - 1:
                return None
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(delay)
    return None


# ---------------------------------------------------------------------------
# NEWS CONSTANTS
# ---------------------------------------------------------------------------
SOURCE_WEIGHTS = {
    "Reuters": 1.0, "Bloomberg": 1.0, "WSJ": 0.9, "Financial Times": 0.9,
    "CNBC": 0.7, "Yahoo Finance": 0.6, "Finviz": 0.6, "MarketWatch": 0.6,
    "TechStock": 0.5, "Google News": 0.4, "Other": 0.4,
}
HIGH_IMPACT_KEYWORDS = [
    "earnings", "guidance", "profit", "revenue", "merger", "acquisition", "deal",
    "regulatory", "antitrust", "ban", "approval", "downgrade", "upgrade", "rating",
    "lawsuit", "investigation", "bankruptcy", "default", "beat", "miss", "record",
    "cut", "hike", "inflation", "fed", "policy",
]

# ---------------------------------------------------------------------------
# NUMERIC SAFETY
# ---------------------------------------------------------------------------

def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


# ---------------------------------------------------------------------------
# WACC & DCF ENGINE  (unchanged logic, hardened)
# ---------------------------------------------------------------------------

def get_risk_free_rate_inst():
    try:
        hist = _with_retry(lambda: yf.Ticker("^TNX").history(period="1d"))
        if hist is not None and not hist.empty:
            return hist['Close'].iloc[-1] / 100.0
    except Exception:
        pass
    return 0.042


def calculate_wacc_institutional(info, financials, balance_sheet):
    try:
        raw_beta = _safe_float(info.get('beta'), 1.0)
        adj_beta = raw_beta * 0.67 + 1.0 * 0.33
        beta_stress = min(adj_beta, 1.60)
        beta_base = min(adj_beta, 1.25)
        rf = max(0.035, min(get_risk_free_rate_inst(), 0.050))
        erp = 0.0525
        ke_stress = rf + beta_stress * erp
        ke_base = rf + beta_base * erp

        market_cap = _safe_float(info.get('marketCap'), 1) or 1
        total_debt = 0
        if balance_sheet is not None and not balance_sheet.empty:
            for col in ['Total Debt', 'Long Term Debt', 'LongTermDebt']:
                if col in balance_sheet.index:
                    v = _safe_float(balance_sheet.loc[col].iloc[0])
                    if v is not None:
                        total_debt = v
                        break

        total_val = market_cap + total_debt
        w_e = market_cap / total_val
        w_d = total_debt / total_val

        interest_expense = 0
        if financials is not None and not financials.empty:
            for col in ['Interest Expense', 'Interest Expense Non Operating']:
                if col in financials.index:
                    v = _safe_float(financials.loc[col].iloc[0])
                    if v is not None:
                        interest_expense = abs(v)
                        break

        tax_rate = 0.21
        if financials is not None and not financials.empty:
            try:
                if 'Tax Provision' in financials.index and 'Pretax Income' in financials.index:
                    taxes = _safe_float(financials.loc['Tax Provision'].iloc[0])
                    pretax = _safe_float(financials.loc['Pretax Income'].iloc[0])
                    if taxes and pretax and pretax != 0:
                        eff = taxes / pretax
                        if 0.15 < eff < 0.30:
                            tax_rate = eff
            except Exception:
                pass

        cost_debt_pre = (interest_expense / total_debt) if (total_debt > 0 and interest_expense > 0) else (rf + 0.015)
        cost_debt_pre = min(cost_debt_pre, 0.10)
        cost_debt_after = cost_debt_pre * (1 - tax_rate)

        wacc_stress = max(0.08, min(w_e * ke_stress + w_d * cost_debt_after, 0.14))
        wacc_base = max(0.07, min(w_e * ke_base + w_d * cost_debt_after, 0.11))
        return wacc_base, wacc_stress
    except Exception as e:
        print(f"[WACC] error: {e}")
        return 0.10, 0.125


def calculate_auto_growth(info):
    try:
        roe = _safe_float(info.get('returnOnEquity'), 0.15) or 0.15
        payout = _safe_float(info.get('payoutRatio'), 0.0) or 0.0
        return max(0.05, min(roe * (1 - payout), 0.25))
    except Exception:
        return 0.10


# ---------------------------------------------------------------------------
# SECTOR CYCLICALITY  → drives FCF lookback window and DCF/Comps blend
# ---------------------------------------------------------------------------
# Higher cyclicality ⇒ longer FCF smoothing window (ride out the business cycle)
# and heavier reliance on relative valuation (peer multiples).
_SECTOR_CYCLICALITY = {
    "Energy": 0.95, "Basic Materials": 0.90, "Industrials": 0.75,
    "Real Estate": 0.70, "Consumer Cyclical": 0.65, "Financial Services": 0.60,
    "Communication Services": 0.40, "Healthcare": 0.35, "Technology": 0.30,
    "Consumer Defensive": 0.25, "Utilities": 0.45,
}


def _sector_cyclicality(sector):
    return _SECTOR_CYCLICALITY.get(sector, 0.50)


def _fcf_lookback_years(sector):
    """
    Dynamic FCF normalization window:
      highly cyclical (Energy/Materials)  → 7 years (smooth the cycle)
      asset-light / growth (Tech/Defensive) → 3 years (prioritise recency)
    """
    c = _sector_cyclicality(sector)
    if c >= 0.85:
        return 7
    if c >= 0.70:
        return 6
    if c >= 0.55:
        return 5
    if c >= 0.40:
        return 4
    return 3


def _extract_row(df, keys, col):
    """Best-effort fetch of df.loc[key][col] for the first matching key."""
    if df is None or getattr(df, "empty", True):
        return None
    for k in keys:
        if k in df.index and col in df.columns:
            v = _safe_float(df.loc[k][col])
            if v is not None:
                return v
    return None


def calculate_normalized_fcf(stock_obj, reported_fcf, sector=None):
    """
    Sector-adjusted normalized free cash flow.

    Methodology:
      • Lookback window is dynamic (3–7y) per sector cyclicality.
      • CAPEX is SMOOTHED: we apply the mean CapEx/Revenue ratio across the
        window to the latest revenue, removing one-off capex spikes/troughs.
      • NWC is NORMALIZED: the actual year's change in net working capital
        (already embedded in OCF) is swapped for the through-cycle mean ΔNWC,
        so a single working-capital swing doesn't distort the run-rate.
    Returns a normalized FCF level (falls back to reported_fcf on any gap).
    """
    try:
        financials = stock_obj.financials
        cashflow = stock_obj.cashflow
        balance = getattr(stock_obj, "balance_sheet", None)
        if financials is None or financials.empty or cashflow is None or cashflow.empty:
            return reported_fcf

        rev_history = None
        for key in ['Total Revenue', 'TotalRevenue', 'Operating Revenue']:
            if key in financials.index:
                rev_history = financials.loc[key]
                break
        if rev_history is None:
            return reported_fcf

        lookback = _fcf_lookback_years(sector)
        common_cols = list(rev_history.index.intersection(cashflow.columns))[:lookback]
        if not common_cols:
            return reported_fcf

        ocf_margins, capex_ratios, nwc_changes = [], [], []
        prev_nwc = None
        for col in common_cols:
            rev = _safe_float(rev_history[col])
            if not rev or rev <= 0:
                continue
            ocf = _extract_row(cashflow, ['Operating Cash Flow', 'Total Cash From Operating Activities'], col)
            if ocf is None:
                continue
            ocf_margins.append(ocf / rev)

            capex = _extract_row(cashflow, ['Capital Expenditure', 'CapitalExpenditures'], col)
            if capex is not None:
                capex_ratios.append(abs(capex) / rev)

            # Net working capital = current assets − current liabilities
            ca = _extract_row(balance, ['Current Assets', 'Total Current Assets'], col)
            cl = _extract_row(balance, ['Current Liabilities', 'Total Current Liabilities'], col)
            if ca is not None and cl is not None:
                nwc = ca - cl
                if prev_nwc is not None:
                    nwc_changes.append((nwc - prev_nwc) / rev)
                prev_nwc = nwc

        if not ocf_margins:
            return reported_fcf

        latest_rev = _safe_float(rev_history.iloc[0]) or 0
        mean_ocf_margin = sum(ocf_margins) / len(ocf_margins)
        mean_capex_ratio = (sum(capex_ratios) / len(capex_ratios)) if capex_ratios else 0.0

        # Smoothed FCF margin = through-cycle OCF margin − smoothed CapEx intensity
        normalized_margin = mean_ocf_margin - mean_capex_ratio

        # NWC normalization: nudge toward the through-cycle mean ΔNWC drag.
        if nwc_changes:
            mean_nwc_drag = sum(nwc_changes) / len(nwc_changes)
            # A persistent build in NWC (positive ΔNWC) is a cash drag.
            normalized_margin -= max(0.0, mean_nwc_drag) * 0.5

        normalized = latest_rev * normalized_margin
        if normalized <= 0:
            return reported_fcf
        # Blend reported & normalized so a single noisy statement can't dominate.
        return reported_fcf * 0.4 + normalized * 0.6 if reported_fcf > 0 else normalized
    except Exception:
        return reported_fcf


def calculate_blend_weights(info, sector):
    """
    Bayesian-inspired DCF/Comps blend. Returns (w_dcf, w_comps) summing to 1.

    Intuition: DCF is most trustworthy for predictable, asset-light businesses;
    relative valuation is more reliable for cyclical / capital-intensive firms
    where forward cash flows are hard to forecast but peer multiples are dense.

    A 'DCF reliability' prior in [0,1] is built from:
      • sector cyclicality (lower ⇒ more DCF)
      • capex intensity     (lower ⇒ more DCF)
      • beta / volatility   (lower ⇒ more DCF)
      • profitability       (positive, stable margins ⇒ more DCF)
    Mapped to a DCF weight in [0.30, 0.80].
    """
    try:
        cyc = _sector_cyclicality(sector)
        reliability = 1.0 - cyc  # asset-light sectors start high

        revenue = _safe_float(info.get('totalRevenue'), 0) or 0
        capex = abs(_safe_float(info.get('capitalExpenditures'), 0) or 0)
        capex_intensity = (capex / revenue) if revenue > 0 else 0.10
        # capex intensity 0 → +0.15, 0.20+ → −0.15
        reliability += max(-0.15, min(0.15, (0.10 - capex_intensity) * 1.5))

        beta = _safe_float(info.get('beta'), 1.0) or 1.0
        # beta 0.5 → +0.10, beta 2.0 → −0.20
        reliability += max(-0.20, min(0.10, (1.0 - beta) * 0.20))

        margin = _safe_float(info.get('profitMargins'), 0) or 0
        if margin >= 0.15:
            reliability += 0.10
        elif margin <= 0:
            reliability -= 0.15

        reliability = max(0.0, min(1.0, reliability))
        w_dcf = round(0.30 + reliability * 0.50, 2)   # ∈ [0.30, 0.80]
        w_dcf = max(0.30, min(0.80, w_dcf))
        return w_dcf, round(1.0 - w_dcf, 2)
    except Exception:
        return 0.60, 0.40


def run_institutional_dcf(ticker, info, stock_obj, sector_benchmark_pe=20):
    try:
        shares = _safe_float(info.get('sharesOutstanding'))
        price = _safe_float(info.get('currentPrice')) or _safe_float(info.get('regularMarketPrice'))
        cf = stock_obj.cashflow
        latest_fcf = 0
        if cf is not None and not cf.empty:
            for name in ['Free Cash Flow', 'FreeCashFlow']:
                if name in cf.index:
                    v = _safe_float(cf.loc[name].iloc[0])
                    if v is not None:
                        latest_fcf = v
                        break
        if latest_fcf == 0:
            latest_fcf = _safe_float(info.get('freeCashflow'), 0) or 0
        if not shares or not price or latest_fcf <= 0:
            p = price or 0
            return {"val": p, "low": p * 0.9, "high": p * 1.1, "wacc": 0.1, "growth": 0.05}, "Insufficient Data"

        fcf_start = calculate_normalized_fcf(stock_obj, latest_fcf, sector=info.get('sector'))
        wacc_base, wacc_stress = calculate_wacc_institutional(info, stock_obj.financials, stock_obj.balance_sheet)
        growth_explicit = calculate_auto_growth(info)
        growth_terminal = 0.025

        future_fcf = []
        cur = fcf_start
        for _ in range(1, 6):
            cur *= 1 + growth_explicit
            future_fcf.append(cur)
        decay = (growth_explicit - growth_terminal) / 5
        g = growth_explicit
        for _ in range(6, 11):
            g -= decay
            cur *= 1 + g
            future_fcf.append(cur)

        tv_perp = future_fcf[-1] * (1 + growth_terminal) / (wacc_base - growth_terminal)
        df_base = [1 / (1 + wacc_base) ** i for i in range(1, 11)]
        pv_fcf = sum(f * d for f, d in zip(future_fcf, df_base))
        pv_tv = tv_perp / (1 + wacc_base) ** 10
        net_debt = (_safe_float(info.get('totalDebt'), 0) or 0) - (_safe_float(info.get('totalCash'), 0) or 0)
        equity_base = pv_fcf + pv_tv - net_debt
        share_base = equity_base / shares

        tv_stress = future_fcf[-1] * (1 + growth_terminal) / (wacc_stress - growth_terminal)
        df_stress = [1 / (1 + wacc_stress) ** i for i in range(1, 11)]
        pv_fcf_s = sum(f * d for f, d in zip(future_fcf, df_stress))
        equity_stress = pv_fcf_s + tv_stress / (1 + wacc_stress) ** 10 - net_debt
        share_stress = equity_stress / shares

        try:
            ebitda = _safe_float(info.get('ebitda'), 0) or 0
            if ebitda > 0:
                ebitda_y10 = ebitda * (1 + growth_explicit) ** 5 * (1 + (growth_explicit + growth_terminal) / 2) ** 5
                tv_exit = ebitda_y10 * min(sector_benchmark_pe, 25)
                pv_tv_exit = tv_exit / (1 + wacc_base) ** 10
                eq_exit = pv_fcf + pv_tv_exit - net_debt
                share_exit = eq_exit / shares
                if share_exit > 0:
                    share_base = share_base * 0.7 + share_exit * 0.3
        except Exception:
            pass

        return {
            "val": round(share_base, 2),
            "low": round(share_stress, 2),
            "high": round(share_base * 1.25, 2),
            "wacc": round(wacc_base * 100, 1),
            "growth": round(growth_explicit * 100, 1),
            "fcf_start": fcf_start,
            "fcf_lookback": _fcf_lookback_years(info.get('sector')),
        }, "Institutional DCF"
    except Exception as e:
        print(f"[DCF] error: {e}")
        return {"val": 0, "low": 0, "high": 0, "wacc": 0, "growth": 0}, "Error"


# ---------------------------------------------------------------------------
# SMART NEWS ENGINE
# ---------------------------------------------------------------------------

def parse_news_date(date_obj):
    if isinstance(date_obj, (int, float)):
        return datetime.fromtimestamp(date_obj)
    if isinstance(date_obj, str):
        try:
            return datetime.fromtimestamp(int(date_obj))
        except Exception:
            pass
        try:
            return datetime.strptime(date_obj, "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            pass
    return datetime.now()


def calculate_time_decay(pub_time, half_life_days=3):
    try:
        age_days = (datetime.now() - pub_time).total_seconds() / 86400
        return math.exp(-math.log(2) * age_days / half_life_days)
    except Exception:
        return 1.0


def directional_adjustment(title):
    t = title.lower()
    pos = ["beat", "record", "surge", "jump", "soar", "strong", "upgrade", "buy", "growth"]
    neg = ["miss", "fail", "drop", "plunge", "weak", "downgrade", "sell", "loss", "risk"]
    return 0.3 if any(x in t for x in pos) else (-0.3 if any(x in t for x in neg) else 0)


def is_relevant_news(title):
    return any(k in title.lower() for k in HIGH_IMPACT_KEYWORDS)


def analyze_headline_institutional(headline, sentiment=None):
    """
    Score a single headline.
    `sentiment` — optional precomputed compound score in [-1,1] from the FinBERT
    batch engine. When supplied we trust the contextual model and skip the VADER
    keyword heuristic (which would otherwise mis-read nuanced financial phrasing).
    """
    title = headline.get('title', '')
    source = headline.get('publisher', 'Other')
    pub_time = parse_news_date(headline.get('providerPublishTime', datetime.now()))
    if sentiment is not None:
        combined_raw = max(min(float(sentiment), 1.0), -1.0)
    else:
        analyzer = SentimentIntensityAnalyzer()
        vader_score = analyzer.polarity_scores(title)['compound']
        combined_raw = max(min(vader_score + directional_adjustment(title), 1.0), -1.0)
    source_w = SOURCE_WEIGHTS.get(source, 0.4)
    relevance_w = 1.2 if is_relevant_news(title) else 0.6
    final_score = combined_raw * source_w * relevance_w * calculate_time_decay(pub_time)
    return {
        "title": title,
        "link": headline.get('link'),
        "publisher": source,
        "timestamp": pub_time,
        "raw_sentiment": combined_raw,
        "impact_score": final_score,
        "is_relevant": relevance_w > 1.0,
    }


_GEO_MAP = {
    "India": ("en-IN", "IN"), "UK": ("en-GB", "GB"), "Europe": ("en-GB", "GB"),
    "Japan": ("ja-JP", "JP"), "Hong Kong": ("en-HK", "HK"), "China": ("en-CN", "CN"),
    "Canada": ("en-CA", "CA"), "Australia": ("en-AU", "AU"), "USA": ("en-US", "US"),
}


def _normalize_yf_news(yf_news):
    """
    Normalise yfinance .news into the flat schema the analyzer expects.
    Handles BOTH the legacy flat format (title/link/providerPublishTime)
    and the newer nested {'content': {...}} format introduced in yfinance 0.2.40+.
    """
    out = []
    for item in (yf_news or []):
        if not isinstance(item, dict):
            continue
        # New nested format
        content = item.get('content')
        if isinstance(content, dict):
            title = content.get('title', '')
            # link can live in several places
            link = ''
            cu = content.get('canonicalUrl') or content.get('clickThroughUrl') or {}
            if isinstance(cu, dict):
                link = cu.get('url', '')
            provider = (content.get('provider') or {})
            publisher = provider.get('displayName', 'Yahoo Finance') if isinstance(provider, dict) else 'Yahoo Finance'
            pub = content.get('pubDate') or content.get('displayTime') or ''
            out.append({
                "title": title, "link": link, "publisher": publisher,
                "providerPublishTime": pub,
            })
        else:
            # Legacy flat format
            out.append({
                "title": item.get('title', ''),
                "link": item.get('link', ''),
                "publisher": item.get('publisher', 'Yahoo Finance'),
                "providerPublishTime": item.get('providerPublishTime', ''),
            })
    return [h for h in out if h.get('title')]


def _fetch_rss(url, default_publisher="Google News", limit=12):
    """Generic, defensive RSS/XML fetcher. Returns a list of flat headline dicts."""
    def _fetch():
        r = requests.get(url, headers=_rand_ua(), timeout=8)
        # lxml-xml first; fall back to html parser if the feed is malformed
        try:
            soup = BeautifulSoup(r.content, "xml")
        except Exception:
            soup = BeautifulSoup(r.content, "html.parser")
        items = soup.find_all("item") or soup.find_all("entry")
        results = []
        for item in items[:limit]:
            title_el = item.find("title")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue
            # link: <link>text</link> OR <link href="..."/> (Atom)
            link_el = item.find("link")
            link = ""
            if link_el is not None:
                link = link_el.get_text(strip=True) or link_el.get("href", "")
            src_el = item.find("source")
            publisher = src_el.get_text(strip=True) if (src_el and src_el.get_text(strip=True)) else default_publisher
            date_el = item.find("pubDate") or item.find("published") or item.find("updated")
            pub = date_el.get_text(strip=True) if date_el else ""
            results.append({
                "title": title, "link": link,
                "publisher": publisher, "providerPublishTime": pub,
            })
        return results

    return _with_retry(_fetch, retries=2, base_delay=1.0) or []


def fetch_google_news(query, country_name="USA", limit=12):
    """Google News RSS with a broadened, finance-weighted query."""
    hl, gl = _GEO_MAP.get(country_name, ("en-US", "US"))
    q = f"{query} stock OR shares OR earnings OR finance"
    url = (
        f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}"
        f"&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"
    )
    return _fetch_rss(url, default_publisher="Google News", limit=limit)


def fetch_yahoo_rss(ticker, limit=12):
    """Yahoo Finance per-ticker RSS feed."""
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(ticker)}&region=US&lang=en-US"
    return _fetch_rss(url, default_publisher="Yahoo Finance", limit=limit)


def fetch_sector_news(sector, industry, country_name="USA", limit=8):
    """Final fallback: broad sector/industry macro news so the UI is never empty."""
    topic = industry or sector or "stock market"
    return fetch_google_news(f"{topic} sector", country_name, limit=limit)


# Backwards-compatible alias (older callers / tests)
def fetch_google_news_institutional(company_name, country_name):
    return fetch_google_news(company_name, country_name)


def get_smart_news(ticker, info=None, is_us=False):
    """
    Resilient, multi-tier news pipeline. Tiers run until we have enough
    headlines; the UI never ends up empty thanks to a sector-macro fallback.
        Tier 1  yfinance structured news (normalised for new + legacy schema)
        Tier 2  Finviz scrape (US)
        Tier 3  Yahoo RSS + Google News RSS (broadened query)
        Tier 4  Sector / industry macro news
    """
    company_name = ticker
    country = "USA"
    sector = industry = None
    if info:
        company_name = (
            info.get('shortName') or info.get('longName') or ticker
        ).replace(" Ltd.", "").replace(" Inc.", "").replace(" Corp.", "").strip()
        currency = info.get('currency', 'USD')
        if currency in GLOBAL_MACRO:
            country = GLOBAL_MACRO[currency].get('country', 'USA')
        sector = info.get('sector')
        industry = info.get('industry')

    raw_headlines = []

    def _need_more(n=6):
        return len(raw_headlines) < n

    # ── Tier 1: yfinance structured news ──────────────────────────────────────
    try:
        yf_n = _with_retry(lambda: yf.Ticker(ticker).news, retries=2)
        raw_headlines.extend(_normalize_yf_news(yf_n))
    except Exception:
        pass

    # ── Tier 2: Finviz scrape (US listings only) ──────────────────────────────
    if is_us and _need_more():
        try:
            _, f_news = FinvizService.get_stock_data(ticker)
            if f_news:
                raw_headlines.extend(f_news)
        except Exception:
            pass

    # ── Tier 3: Yahoo RSS + Google News RSS (broadened) ───────────────────────
    if _need_more():
        try:
            raw_headlines.extend(fetch_yahoo_rss(ticker))
        except Exception:
            pass
    if _need_more():
        try:
            raw_headlines.extend(fetch_google_news(company_name, country))
        except Exception:
            pass

    # ── Tier 4: Sector / industry macro fallback (never leave UI empty) ───────
    sector_fallback_used = False
    if _need_more(3):
        try:
            sec = fetch_sector_news(sector, industry, country)
            if sec:
                sector_fallback_used = True
                raw_headlines.extend(sec)
        except Exception:
            pass

    # Dedupe first, then batch-score titles through the FinBERT engine in ONE
    # call (falls back to VADER internally) to keep latency bounded.
    unique, seen = [], set()
    for h in raw_headlines:
        if not h or not h.get('title'):
            continue
        key = (h.get('link') or '').strip() or h.get('title', '').strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)

    try:
        sentiment_map = nlp_batch_sentiment([h.get('title', '') for h in unique])
    except Exception:
        sentiment_map = {}

    processed = [
        analyze_headline_institutional(h, sentiment=sentiment_map.get(h.get('title', '')))
        for h in unique
    ]

    if not processed:
        return {
            "score": 50, "short_term": 50, "medium_term": 50,
            "event_risk": "Low", "fallback": "none", "nlp_engine": nlp_engine_name(),
        }, []

    processed.sort(key=lambda x: x['timestamp'], reverse=True)
    short_score = sum(x['impact_score'] for x in processed[:5]) / min(len(processed), 5)
    med_items = processed[5:15]
    med_score = sum(x['impact_score'] for x in med_items) / len(med_items) if med_items else short_score * 0.5
    max_impact = max((abs(x['impact_score']) for x in processed if x['is_relevant']), default=0)
    event_risk = "High" if max_impact > 0.5 else "Medium" if max_impact > 0.25 else "Low"

    def norm(s):
        return round(min(max((s + 1) * 50, 0), 100), 1)

    return {
        "score": norm(short_score),
        "short_term": norm(short_score),
        "medium_term": norm(med_score),
        "event_risk": event_risk,
        "fallback": "sector" if sector_fallback_used else "direct",
        "nlp_engine": nlp_engine_name(),
    }, processed[:8]


# ---------------------------------------------------------------------------
# PEER ENGINE — CATEGORY A (Direct) & CATEGORY B (Scale)
# ---------------------------------------------------------------------------

def calculate_peer_distance(target, peer):
    weights = {"growth": 0.25, "margin": 0.25, "roic": 0.20, "capex_intensity": 0.15, "net_debt_ebitda": 0.15}
    dist = 0
    for k, w in weights.items():
        t = _safe_float(target.get(k))
        p = _safe_float(peer.get(k))
        if t is None or p is None:
            dist += w * 1.0
        else:
            diff = abs(t - p)
            diff = min(diff / 5.0, 1.0) if k == 'net_debt_ebitda' else min(diff, 1.0)
            dist += w * diff
    return round(max(0, 100 * (1 - dist)), 1)


def process_peer_data(ticker, target_currency, target_profile=None, category="A"):
    """
    Fetches and normalises data for a single peer ticker.
    Returns None on any failure — never raises.
    category: 'A' = direct sector+industry, 'B' = sector-level scale benchmark
    """
    try:
        p_stk = _with_retry(lambda: yf.Ticker(ticker), retries=2)
        if p_stk is None:
            return None
        p_inf = p_stk.info or {}
        if not p_inf:
            return None

        raw_price, raw_curr = normalize_data(p_inf)
        if not raw_price:
            return None

        x_rate = _safe_float(get_exchange_rate(raw_curr, target_currency), 1.0) or 1.0
        price_norm = raw_price * x_rate
        cap_norm = (_safe_float(p_inf.get('marketCap'), 0) or 0) * x_rate
        ebitda = _safe_float(p_inf.get('ebitda'), 1) or 1
        revenue = _safe_float(p_inf.get('totalRevenue'), 1) or 1
        capex = abs(_safe_float(p_inf.get('capitalExpenditures'), 0) or 0)
        total_debt = _safe_float(p_inf.get('totalDebt'), 0) or 0
        total_cash = _safe_float(p_inf.get('totalCash'), 0) or 0

        peer_prof = {
            'mkt_cap': cap_norm,
            'growth': _safe_float(p_inf.get('revenueGrowth'), 0),
            'margin': _safe_float(p_inf.get('ebitdaMargins'), 0),
            'roic': _safe_float(p_inf.get('returnOnEquity'), 0),
            'capex_intensity': capex / revenue,
            'net_debt_ebitda': (total_debt - total_cash) / ebitda,
        }

        sim = calculate_peer_distance(target_profile, peer_prof) if target_profile else 50

        return {
            "symbol": ticker,
            "name": p_inf.get('shortName', ticker),
            "price": round(price_norm, 2),
            "mkt_cap": format_large_number(cap_norm),
            "pe": _safe_float(p_inf.get('trailingPE')),
            "ev_ebitda": _safe_float(p_inf.get('enterpriseToEbitda')),
            "ev_sales": _safe_float(p_inf.get('enterpriseToRevenue')),
            "net_debt_ebitda": round(peer_prof['net_debt_ebitda'], 2),
            "roic": f"{round((peer_prof['roic'] or 0) * 100, 1)}%",
            "similarity": sim,
            "growth_raw": _safe_float(p_inf.get('revenueGrowth'), 0),
            "sector": p_inf.get('sector', 'N/A'),
            "industry": p_inf.get('industry', 'N/A'),
            "employees": format_large_number(p_inf.get('fullTimeEmployees', 0)),
            "debt_equity": round(_safe_float(p_inf.get('debtToEquity'), 0), 1),
            "website": p_inf.get('website'),
            "city": p_inf.get('city'),
            "country": p_inf.get('country'),
            "summary": (
                p_inf.get('longBusinessSummary') or
                p_inf.get('shortBusinessSummary') or
                p_inf.get('summary')
            ),
            "profile": peer_prof,
            "category": category,   # 'A' = direct, 'B' = scale benchmark
        }
    except Exception as exc:
        print(f"[process_peer_data] {ticker}: {exc}")
        return None


def get_robust_peers(symbol, target_sector, target_industry, target_mkt_cap,
                     is_us=False, target_revenue=None, target_roic=None, target_margin=None):
    """
    Formalized, cascading peer selection.

    Category A (Direct comparables) is built by a rigid filter cascade:
        Filter 1  Sector AND Industry match               (always required)
        Filter 2  Revenue band   — within ±30% of target
        Filter 3  Market-cap band — within ±50% of target
        Filter 4  ROIC & EBITDA-margin similarity         (used for ranking)
    If the strictest tier yields < 3 names, the bands are RELAXED one tier at a
    time (documented), so thin-coverage / international names still get a cohort.

    Category B (Scale benchmarks) = same sector, different industry, sized 0.2x–5x.

    Returns {"cat_a": [...], "cat_b": [...], "methodology": {...}} where the
    methodology dict is surfaced in the UI so reviewers can see exactly how the
    cohort was generated.
    """
    raw_candidates: list[str] = []

    # Source 1: Finviz screener (US, sector+industry filtered)
    if is_us:
        try:
            fv = FinvizService.get_peers(target_sector, target_industry, str(target_mkt_cap)) or []
            raw_candidates.extend(fv)
        except Exception:
            pass

    # Source 2: yahooquery recommendations (works internationally)
    try:
        t = YQTicker(symbol)
        recs = _with_retry(lambda: t.recommendations, retries=2)
        if recs is not None and symbol in recs:
            rec_syms = [i['symbol'] for i in recs[symbol].get('recommendedSymbols', [])]
            raw_candidates.extend(rec_syms)
    except Exception:
        pass

    seen = set()
    deduped = []
    for c in raw_candidates:
        if c != symbol and c not in seen:
            seen.add(c)
            deduped.append(c)

    methodology = {
        "filters": [
            "GICS Sector & Industry match",
            "Revenue band ±30% of target",
            "Market cap ±50% of target",
            "ROIC & margin similarity (ranking)",
        ],
        "tier_used": "none",
        "candidates_screened": len(deduped),
        "note": "Category A = direct comparables; Category B = same-sector scale benchmarks.",
    }

    if not deduped:
        return {"cat_a": [], "cat_b": [], "methodology": methodology}

    # ── Fetch candidate metadata once ─────────────────────────────────────────
    batch = deduped[:20]
    metas = []
    try:
        objs = _with_retry(lambda: yf.Tickers(" ".join(batch)), retries=2)
        if objs is None:
            methodology["tier_used"] = "unfiltered (metadata unavailable)"
            return {"cat_a": deduped[:6], "cat_b": [], "methodology": methodology}
        for p in batch:
            try:
                pi = objs.tickers[p].info or {}
                if not pi:
                    continue
                metas.append({
                    "symbol": p,
                    "sector": pi.get('sector'),
                    "industry": pi.get('industry'),
                    "mkt_cap": _safe_float(pi.get('marketCap'), 0) or 0,
                    "revenue": _safe_float(pi.get('totalRevenue'), 0) or 0,
                    "roic": _safe_float(pi.get('returnOnEquity'), 0),
                    "margin": _safe_float(pi.get('ebitdaMargins'), 0),
                })
            except Exception:
                pass
    except Exception:
        methodology["tier_used"] = "unfiltered (batch fetch failed)"
        return {"cat_a": deduped[:6], "cat_b": [], "methodology": methodology}

    same_sector = [m for m in metas if m["sector"] == target_sector]
    same_industry = [m for m in same_sector if m["industry"] == target_industry]

    def _within(value, target, pct):
        if not target or not value:
            return True  # can't test → don't exclude
        return target * (1 - pct) <= value <= target * (1 + pct)

    # ── Category A cascade with documented relaxation ─────────────────────────
    tiers = [
        ("Sector+Industry, Revenue ±30%, MktCap ±50%",
         lambda m: _within(m["revenue"], target_revenue, 0.30) and _within(m["mkt_cap"], target_mkt_cap, 0.50)),
        ("Sector+Industry, MktCap ±50%",
         lambda m: _within(m["mkt_cap"], target_mkt_cap, 0.50)),
        ("Sector+Industry, MktCap 0.1x–10x",
         lambda m: (not target_mkt_cap or not m["mkt_cap"] or target_mkt_cap * 0.1 <= m["mkt_cap"] <= target_mkt_cap * 10)),
        ("Sector+Industry (size unconstrained)", lambda m: True),
    ]

    cat_a_metas = []
    for label, pred in tiers:
        cat_a_metas = [m for m in same_industry if pred(m)]
        if len(cat_a_metas) >= 3:
            methodology["tier_used"] = label
            break
    else:
        # loop didn't break — use whatever the loosest tier produced
        methodology["tier_used"] = tiers[-1][0] if same_industry else "Sector-only (no industry match)"

    # Rank Category A by combined size + ROIC + margin proximity to the target,
    # so that even when the hard size band is relaxed the closest-by-scale names
    # surface first (prevents a mega-cap being matched to micro-caps).
    def _similarity_key(m):
        score = 0.0
        if target_mkt_cap and m["mkt_cap"] and target_mkt_cap > 0 and m["mkt_cap"] > 0:
            score += abs(math.log10(m["mkt_cap"]) - math.log10(target_mkt_cap)) * 2.0
        if target_roic is not None and m["roic"] is not None:
            score += abs(target_roic - m["roic"])
        if target_margin is not None and m["margin"] is not None:
            score += abs(target_margin - m["margin"])
        return score
    cat_a_metas.sort(key=_similarity_key)
    cat_a = [m["symbol"] for m in cat_a_metas]

    # ── Category B: same sector, different industry, sized 0.2x–5x ────────────
    cat_b = []
    for m in same_sector:
        if m["industry"] == target_industry:
            continue
        if not target_mkt_cap or not m["mkt_cap"] or (target_mkt_cap * 0.2 <= m["mkt_cap"] <= target_mkt_cap * 5):
            cat_b.append(m["symbol"])

    # If no direct industry match exists, promote sized sector peers into A
    if not cat_a and cat_b:
        methodology["tier_used"] = "Sector-only fallback (no industry match found)"
        cat_a = cat_b[:4]
        cat_b = cat_b[4:]

    methodology["cat_a_count"] = len(cat_a[:8])
    methodology["cat_b_count"] = len(cat_b[:6])
    return {"cat_a": cat_a[:8], "cat_b": cat_b[:6], "methodology": methodology}


def calculate_weighted_harmonic_mean(vals, weights):
    try:
        cv, cw = [], []
        for v, w in zip(vals, weights):
            fv = _safe_float(v)
            fw = _safe_float(w)
            if fv and fv > 0 and fw is not None:
                cv.append(fv)
                cw.append(fw)
        if not cv:
            return 0
        return sum(cw) / sum(w / v for w, v in zip(cw, cv))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# ULTRA-EXPANDED FINANCIAL STATEMENTS
# ---------------------------------------------------------------------------

def _safe_get(df, keys, col):
    for k in (keys if isinstance(keys, list) else [keys]):
        if k in df.index:
            try:
                val = df.loc[k, col]
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    return format_large_number(val)
            except Exception:
                pass
    return "-"


def _pct(df, num_keys, den_keys, col):
    try:
        num = None
        for k in (num_keys if isinstance(num_keys, list) else [num_keys]):
            if k in df.index:
                v = _safe_float(df.loc[k, col])
                if v is not None:
                    num = v
                    break
        den = None
        for k in (den_keys if isinstance(den_keys, list) else [den_keys]):
            if k in df.index:
                v = _safe_float(df.loc[k, col])
                if v is not None:
                    den = v
                    break
        if num is not None and den and den != 0:
            return f"{round(num / den * 100, 1)}%"
    except Exception:
        pass
    return "-"


def get_financial_statements(stock_obj):
    result = {"income": [], "balance": [], "cashflow": []}
    try:
        inc = stock_obj.income_stmt
        bal = stock_obj.balance_sheet
        cf = stock_obj.cashflow

        # Collect date columns from whichever statement is available
        cols = []
        for df in [inc, bal, cf]:
            if df is not None and not df.empty:
                cols = list(df.columns[:4])
                break

        for col in cols:
            date_str = col.strftime('%Y-%m-%d') if hasattr(col, 'strftime') else str(col)

            # ── Income Statement ────────────────────────────────────────────
            if inc is not None and not inc.empty and col in inc.columns:
                rev_val = None
                for rk in ['Total Revenue', 'TotalRevenue', 'Operating Revenue']:
                    if rk in inc.index:
                        v = _safe_float(inc.loc[rk, col])
                        if v is not None:
                            rev_val = v
                            break
                result["income"].append({
                    "date": date_str,
                    "Revenue": format_large_number(rev_val) if rev_val else "-",
                    "Gross Profit": _safe_get(inc, ['Gross Profit', 'GrossProfit'], col),
                    "Operating Income": _safe_get(inc, ['Operating Income', 'EBIT', 'OperatingIncome'], col),
                    "EBITDA": _safe_get(inc, ['EBITDA', 'Normalized EBITDA', 'Ebitda'], col),
                    "Net Income": _safe_get(inc, ['Net Income', 'Net Income Common Stockholders',
                                                   'Net Income From Continuing Operation Net Minority Interest',
                                                   'NetIncome'], col),
                    "EPS (Diluted)": _safe_get(inc, ['Diluted EPS', 'Basic EPS'], col),
                    "R&D": _safe_get(inc, ['Research And Development', 'ResearchAndDevelopment'], col),
                    "Gross Margin": _pct(inc, ['Gross Profit', 'GrossProfit'],
                                         ['Total Revenue', 'TotalRevenue', 'Operating Revenue'], col),
                    "Operating Margin": _pct(inc, ['Operating Income', 'EBIT'],
                                             ['Total Revenue', 'TotalRevenue', 'Operating Revenue'], col),
                    "Net Margin": _pct(inc, ['Net Income', 'Net Income Common Stockholders'],
                                       ['Total Revenue', 'TotalRevenue', 'Operating Revenue'], col),
                })

            # ── Balance Sheet ────────────────────────────────────────────────
            if bal is not None and not bal.empty and col in bal.columns:
                # Working Capital: prefer direct key, else compute
                wc = "-"
                if 'Working Capital' in bal.index:
                    v = _safe_float(bal.loc['Working Capital', col])
                    if v is not None:
                        wc = format_large_number(v)
                else:
                    try:
                        ca = None
                        cl = None
                        for k in ['Current Assets', 'Total Current Assets']:
                            if k in bal.index:
                                ca = _safe_float(bal.loc[k, col])
                                break
                        for k in ['Current Liabilities', 'Total Current Liabilities']:
                            if k in bal.index:
                                cl = _safe_float(bal.loc[k, col])
                                break
                        if ca is not None and cl is not None:
                            wc = format_large_number(ca - cl)
                    except Exception:
                        pass

                equity = _safe_get(bal, [
                    'Total Equity Gross Minority Interest', 'Common Stock Equity',
                    'Stockholders Equity', 'Total Stockholder Equity',
                ], col)

                result["balance"].append({
                    "date": date_str,
                    "Total Assets": _safe_get(bal, ['Total Assets', 'TotalAssets'], col),
                    "Total Liabilities": _safe_get(bal, [
                        'Total Liabilities Net Minority Interest', 'Total Liab', 'TotalLiabilities'
                    ], col),
                    "Total Equity": equity,
                    "Total Debt": _safe_get(bal, ['Total Debt', 'Long Term Debt',
                                                   'Long Term Debt And Capital Lease Obligation'], col),
                    "Cash": _safe_get(bal, [
                        'Cash And Cash Equivalents', 'Cash Cash Equivalents And Short Term Investments',
                        'Cash Financial', 'CashAndCashEquivalents'
                    ], col),
                    "Working Capital": wc,
                    "Goodwill": _safe_get(bal, ['Goodwill', 'Goodwill And Other Intangible Assets'], col),
                    "Net PPE": _safe_get(bal, ['Net PPE', 'Net Property Plant And Equipment'], col),
                })

            # ── Cash Flow Statement ──────────────────────────────────────────
            if cf is not None and not cf.empty and col in cf.columns:
                capex_val = None
                for k in ['Capital Expenditure', 'Capital Expenditures', 'CapEx',
                          'Purchase Of Property Plant And Equipment']:
                    if k in cf.index:
                        capex_val = _safe_float(cf.loc[k, col])
                        if capex_val is not None:
                            break

                lev_fcf = unl_fcf = "-"
                try:
                    ocf_v = None
                    for k in ['Operating Cash Flow', 'Total Cash From Operating Activities',
                              'Cash Flow From Continuing Operating Activities']:
                        if k in cf.index:
                            ocf_v = _safe_float(cf.loc[k, col])
                            if ocf_v is not None:
                                break
                    if ocf_v is not None and capex_val is not None:
                        cap = float(capex_val)
                        levered = ocf_v + cap if cap < 0 else ocf_v - cap
                        lev_fcf = format_large_number(levered)
                        unl_fcf = lev_fcf  # approximation (no after-tax interest in CF stmt)
                except Exception:
                    pass

                result["cashflow"].append({
                    "date": date_str,
                    "Operating CF": _safe_get(cf, [
                        'Operating Cash Flow', 'Total Cash From Operating Activities',
                        'Cash Flow From Continuing Operating Activities'
                    ], col),
                    "Capex": format_large_number(capex_val) if capex_val is not None else "-",
                    "Free Cash Flow": _safe_get(cf, ['Free Cash Flow', 'FreeCashFlow'], col),
                    "Levered FCF": lev_fcf,
                    "Unlevered FCF": unl_fcf,
                    "D&A": _safe_get(cf, [
                        'Depreciation And Amortization', 'Depreciation',
                        'Reconciled Depreciation', 'DepreciationAndAmortization'
                    ], col),
                    "SBC": _safe_get(cf, ['Stock Based Compensation', 'Share Based Compensation'], col),
                    "Changes in WC": _safe_get(cf, ['Change In Working Capital',
                                                     'Changes In Working Capital'], col),
                })

    except Exception as exc:
        print(f"[get_financial_statements] error: {exc}")

    return result


# ---------------------------------------------------------------------------
# REMAINING HELPERS
# ---------------------------------------------------------------------------

def get_historical_trading_range(stock_obj):
    try:
        h = stock_obj.history(period="5y", interval="1wk")
        if h.empty:
            return None
        p = h['Close'].values.astype(float)
        return {
            "low_5y": round(float(np.min(p)), 2),
            "high_5y": round(float(np.max(p)), 2),
            "avg_5y": round(float(np.mean(p)), 2),
            "low_52wk": round(float(np.min(p[-52:])), 2),
            "high_52wk": round(float(np.max(p[-52:])), 2),
        }
    except Exception:
        return None


def get_analyst_consensus(ticker, info, is_us=False):
    """
    Pulls real analyst grade counts + price targets from yfinance and yahooquery.
    Priority order:
      1. yfinance info dict  → price targets + recommendationKey
      2. yfinance upgrades_downgrades → grade counts (trailing 12 months)
      3. yahooquery recommendation_trend → aggregated buy/hold/sell counts
      4. yahooquery financial_data → fallback recommendationKey
    Returns data_available=False only when truly nothing is found.
    """
    consensus = {
        "buy": None, "hold": None, "sell": None,
        "recommendation": None,
        "recommendation_mean": None,   # 1.0=Strong Buy → 5.0=Strong Sell (yfinance scale)
        "target_mean": None, "target_high": None, "target_low": None,
        "num_analysts": None,
        "data_available": False,
    }

    # ── 1. Price targets + recommendation key/mean from yfinance info ────────
    try:
        target_mean  = _safe_float(info.get('targetMeanPrice'))
        target_high  = _safe_float(info.get('targetHighPrice'))
        target_low   = _safe_float(info.get('targetLowPrice'))
        num_analysts = info.get('numberOfAnalystOpinions')
        rec_mean_raw = _safe_float(info.get('recommendationMean'))  # 1–5 numeric scale

        if target_mean and target_mean > 0:
            consensus['target_mean']  = round(target_mean, 2)
            consensus['target_high']  = round(target_high, 2) if target_high else None
            consensus['target_low']   = round(target_low, 2)  if target_low  else None
            consensus['num_analysts'] = num_analysts
            consensus['data_available'] = True

        if rec_mean_raw and 1.0 <= rec_mean_raw <= 5.0:
            consensus['recommendation_mean'] = round(rec_mean_raw, 2)
            consensus['data_available'] = True

        rec_key = (info.get('recommendationKey') or '').lower().strip()
        if rec_key and rec_key not in ('none', 'n/a', ''):
            consensus['recommendation'] = rec_key.replace('_', ' ').title()
    except Exception:
        pass

    # ── 2. Grade counts from yfinance upgrades_downgrades (last 12 months) ──
    _BUY_GRADES  = {'buy', 'strong buy', 'overweight', 'outperform', 'add',
                    'accumulate', 'positive', 'top pick'}
    _HOLD_GRADES = {'hold', 'neutral', 'sector perform', 'market perform',
                    'equal weight', 'in-line', 'peer perform', 'mixed'}
    _SELL_GRADES = {'sell', 'strong sell', 'underweight', 'underperform',
                    'reduce', 'negative', 'cautious'}

    try:
        stock  = yf.Ticker(ticker)
        grades = _with_retry(lambda: stock.upgrades_downgrades, retries=2)
        if grades is not None and not grades.empty:
            # Index is a DatetimeIndex (GradeDate); filter trailing 12 months
            try:
                import pandas as pd
                cutoff = datetime.now() - __import__('datetime').timedelta(days=365)
                # Normalise index to tz-naive for comparison
                idx = grades.index
                if hasattr(idx, 'tz_localize'):
                    try:
                        idx = idx.tz_localize(None) if idx.tzinfo is None else idx.tz_convert(None)
                    except Exception:
                        pass
                recent = grades[idx >= pd.Timestamp(cutoff)]
                if recent.empty:
                    recent = grades  # fall back to all time if nothing in last year
            except Exception:
                recent = grades

            buy_c = hold_c = sell_c = 0
            if 'To Grade' in recent.columns:
                col = recent['To Grade'].fillna('').str.lower().str.strip()
                buy_c  = int(col.isin(_BUY_GRADES).sum())
                hold_c = int(col.isin(_HOLD_GRADES).sum())
                sell_c = int(col.isin(_SELL_GRADES).sum())
            elif 'Action' in recent.columns:
                col = recent['Action'].fillna('').str.lower().str.strip()
                buy_c  = int(col.isin({'upgrade', 'buy', 'overweight', 'outperform'}).sum())
                hold_c = int(col.isin({'main', 'hold', 'neutral', 'reit'}).sum())
                sell_c = int(col.isin({'downgrade', 'sell', 'underweight', 'underperform'}).sum())

            if buy_c or hold_c or sell_c:
                consensus['buy']  = buy_c
                consensus['hold'] = hold_c
                consensus['sell'] = sell_c
                consensus['data_available'] = True
    except Exception:
        pass

    # ── 3. yahooquery recommendation_trend (aggregated buy/hold/sell) ────────
    if not consensus['data_available'] or (not consensus['buy'] and not consensus['hold'] and not consensus['sell']):
        try:
            yq   = YQTicker(ticker)
            trend = _with_retry(lambda: yq.recommendation_trend, retries=2)
            if trend is not None and not (hasattr(trend, 'empty') and trend.empty):
                # recommendation_trend returns a DataFrame indexed by period (0m, -1m, -2m, -3m)
                import pandas as pd
                if isinstance(trend, dict) and ticker in trend:
                    trend = trend[ticker]
                if hasattr(trend, 'iloc'):
                    row = trend.iloc[0]  # most recent month
                    buy_c  = int((_safe_float(row.get('strongBuy', 0)) or 0) + (_safe_float(row.get('buy', 0)) or 0))
                    hold_c = int(_safe_float(row.get('hold', 0)) or 0)
                    sell_c = int((_safe_float(row.get('sell', 0)) or 0) + (_safe_float(row.get('strongSell', 0)) or 0))
                    if buy_c or hold_c or sell_c:
                        consensus['buy']  = buy_c
                        consensus['hold'] = hold_c
                        consensus['sell'] = sell_c
                        consensus['data_available'] = True
        except Exception:
            pass

    # ── 4. yahooquery financial_data fallback for recommendationKey ──────────
    if not consensus['recommendation']:
        try:
            yq = YQTicker(ticker)
            fd_raw = _with_retry(lambda: yq.financial_data, retries=1)
            if isinstance(fd_raw, dict) and ticker in fd_raw:
                fd = fd_raw[ticker]
                rk = str(fd.get('recommendationKey', '')).strip().lower()
                if rk and rk not in ('none', 'n/a', ''):
                    consensus['recommendation'] = rk.replace('_', ' ').title()
                    consensus['data_available'] = True
                # Also grab mean target if not already set
                if not consensus['target_mean']:
                    mt = _safe_float(fd.get('targetMeanPrice'))
                    if mt and mt > 0:
                        consensus['target_mean']  = round(mt, 2)
                        consensus['target_high']  = round(_safe_float(fd.get('targetHighPrice')) or mt, 2)
                        consensus['target_low']   = round(_safe_float(fd.get('targetLowPrice'))  or mt, 2)
                        consensus['data_available'] = True
        except Exception:
            pass

    return consensus


# ---------------------------------------------------------------------------
# MODEL VALIDATION / BACKTESTING
# ---------------------------------------------------------------------------

def run_backtest(stock_obj, fair_value, current_price, dcf_low=None, dcf_high=None,
                 consensus_target=None):
    """
    Historical validation of the fair-value estimate against the realized
    trailing-12-month price path.

    METHODOLOGY (read this before trusting the numbers):
      This is a *proximity / convergence* validation, not a look-ahead-free
      point-in-time backtest. Free data sources do not expose the historical
      fundamentals needed to recompute the model at each past date, so instead
      we measure how closely the CURRENT fair value tracks where the stock has
      ACTUALLY traded over the last 12 months:

        MAPE = mean( |fair_value − price_t| / price_t )  over monthly closes
        Hit-ratio = % of months the close fell inside the DCF implied range
        Outperformance = consensus_MAPE − model_MAPE   (positive ⇒ model closer)

      All numbers are computed from real price history — nothing is hardcoded.
      Returns {"data_available": False} when there isn't enough history.
    """
    result = {"data_available": False, "methodology":
              "Proximity of fair value to realized 12-month monthly close path "
              "(not a point-in-time backtest; free data lacks historical fundamentals)."}
    try:
        if not fair_value or fair_value <= 0:
            return result
        hist = _with_retry(lambda: stock_obj.history(period="13mo", interval="1mo"), retries=2)
        if hist is None or hist.empty or 'Close' not in hist.columns:
            return result
        closes = [c for c in hist['Close'].tolist() if c and c > 0]
        if len(closes) < 6:
            return result

        n = len(closes)
        mape = sum(abs(fair_value - p) / p for p in closes) / n * 100

        cons_mape = None
        if consensus_target and consensus_target > 0:
            cons_mape = sum(abs(consensus_target - p) / p for p in closes) / n * 100

        # Hit ratio against the DCF implied range
        hit_ratio = None
        if dcf_low and dcf_high and dcf_high > dcf_low:
            hits = sum(1 for p in closes if dcf_low <= p <= dcf_high)
            hit_ratio = round(hits / n * 100, 1)

        # Directional convergence: did price end the year closer to fair value?
        start_gap = abs(fair_value - closes[0]) / closes[0]
        end_gap = abs(fair_value - closes[-1]) / closes[-1]
        converged = end_gap < start_gap

        actual_return = round((closes[-1] - closes[0]) / closes[0] * 100, 1)

        result.update({
            "data_available": True,
            "window_months": int(n),
            "mape": round(float(mape), 1),
            "consensus_mape": round(float(cons_mape), 1) if cons_mape is not None else None,
            "outperformance": round(float(cons_mape - mape), 1) if cons_mape is not None else None,
            "hit_ratio": round(float(hit_ratio), 1) if hit_ratio is not None else None,
            "converged": bool(converged),
            "actual_return": round(float(actual_return), 1),
        })
    except Exception as e:
        print(f"[backtest] {e}")
    return result


def calculate_quality_scores(info, financials):
    scores = {'Growth': 50, 'Profitability': 50, 'Solvency': 50, 'Value': 50, 'Momentum': 50}
    try:
        rev_g = _safe_float(info.get('revenueGrowth'), 0) or 0
        scores['Growth'] = min(max(int(rev_g * 100) + 50, 20), 99)
        margin = _safe_float(info.get('profitMargins'), 0) or 0
        scores['Profitability'] = min(max(int(margin * 100) + 50, 20), 99)
        de = _safe_float(info.get('debtToEquity'), 50) or 50
        scores['Solvency'] = max(100 - int(de / 3), 20)
        pe = _safe_float(info.get('trailingPE'), 25) or 25
        scores['Value'] = max(100 - int(pe), 20)
        curr = _safe_float(info.get('currentPrice'), 1) or 1
        low = _safe_float(info.get('fiftyTwoWeekLow'), 1) or 1
        high = _safe_float(info.get('fiftyTwoWeekHigh'), 2) or 2
        if high > low:
            scores['Momentum'] = int(((curr - low) / (high - low)) * 100)
    except Exception:
        pass
    return [{"subject": k, "A": v, "fullMark": 100} for k, v in scores.items()]


def generate_swot(info, scores):
    swot = {"Strengths": [], "Weaknesses": [], "Opportunities": [], "Threats": []}
    margin = _safe_float(info.get('profitMargins'), 0) or 0
    de = _safe_float(info.get('debtToEquity'), 0) or 0
    rev_g = _safe_float(info.get('revenueGrowth'), 0) or 0
    pe = _safe_float(info.get('trailingPE'), 0) or 0
    sector = info.get('sector', 'General')

    if margin > 0.15:
        swot['Strengths'].append(f"High profit margins ({margin:.1%}) reflect pricing power")
    if rev_g > 0.10:
        swot['Strengths'].append(f"Strong revenue growth ({rev_g:.1%}) signals market expansion")
    if de < 50:
        swot['Strengths'].append("Conservative balance sheet with low leverage")
    if not swot['Strengths']:
        swot['Strengths'].append("Established brand and market presence")

    if de > 150:
        swot['Weaknesses'].append(f"High financial leverage (D/E: {de:.0f}%) limits flexibility")
    if margin < 0.05:
        swot['Weaknesses'].append(f"Thin margins ({margin:.1%}) vulnerable to cost pressure")
    if not swot['Weaknesses']:
        swot['Weaknesses'].append("Limited international diversification")

    swot['Opportunities'].append(f"Secular growth tailwinds in {sector}")
    if rev_g < 0.05:
        swot['Opportunities'].append("Potential for margin expansion through operational efficiency")
    swot['Opportunities'].append("Strategic M&A optionality at current scale")

    swot['Threats'].append("Intensifying competitive dynamics in core markets")
    if pe > 30:
        swot['Threats'].append(f"Premium valuation (P/E: {pe:.1f}x) sensitive to multiple compression")
    swot['Threats'].append("Macro headwinds: rate environment and FX exposure")

    return swot


def calculate_confidence_score(peers, dcf, info, sentiment_risk):
    score = 90
    if not dcf:
        score -= 20
    if len(peers) < 3:
        score -= 15
    if sentiment_risk == "High":
        score -= 10
    elif sentiment_risk == "Medium":
        score -= 5
    return max(score, 10)
