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


def calculate_normalized_fcf(stock_obj, reported_fcf):
    try:
        financials = stock_obj.financials
        cashflow = stock_obj.cashflow
        if financials is None or financials.empty or cashflow is None or cashflow.empty:
            return reported_fcf
        rev_history = None
        for key in ['Total Revenue', 'TotalRevenue', 'Operating Revenue']:
            if key in financials.index:
                rev_history = financials.loc[key]
                break
        if rev_history is None:
            return reported_fcf
        common_cols = rev_history.index.intersection(cashflow.columns)
        margins = []
        for col in list(common_cols)[:3]:
            try:
                ocf = 0
                for k in ['Operating Cash Flow', 'Total Cash From Operating Activities']:
                    if k in cashflow.index:
                        v = _safe_float(cashflow.loc[k][col])
                        if v is not None:
                            ocf = v
                            break
                capex = 0
                if 'Capital Expenditure' in cashflow.index:
                    capex = _safe_float(cashflow.loc['Capital Expenditure'][col]) or 0
                fcf = ocf + capex if capex < 0 else ocf - capex
                rev = _safe_float(rev_history[col])
                if rev and rev > 0:
                    margins.append(fcf / rev)
            except Exception:
                pass
        if not margins:
            return reported_fcf
        normalized = rev_history.iloc[0] * (sum(margins) / len(margins))
        return max(reported_fcf, normalized) if normalized > 0 else reported_fcf
    except Exception:
        return reported_fcf


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

        fcf_start = calculate_normalized_fcf(stock_obj, latest_fcf)
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


def analyze_headline_institutional(headline):
    title = headline.get('title', '')
    source = headline.get('publisher', 'Other')
    pub_time = parse_news_date(headline.get('providerPublishTime', datetime.now()))
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


def fetch_google_news_institutional(company_name, country_name):
    geo_map = {
        "India": ("en-IN", "IN"), "UK": ("en-GB", "GB"), "Europe": ("en-GB", "GB"),
        "Japan": ("ja-JP", "JP"), "Hong Kong": ("en-HK", "HK"), "China": ("en-CN", "CN"),
        "Canada": ("en-CA", "CA"), "Australia": ("en-AU", "AU"),
    }
    hl, gl = geo_map.get(country_name, ("en-US", "US"))
    q = f"{company_name} stock finance"
    url = (
        f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}"
        f"&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"
    )

    def _fetch():
        r = requests.get(url, headers=_rand_ua(), timeout=8)
        soup = BeautifulSoup(r.content, "xml")
        return [
            {
                "title": item.title.text if item.title else "",
                "link": item.link.text if item.link else "",
                "publisher": item.source.text if item.source else "Google News",
                "providerPublishTime": item.pubDate.text if item.pubDate else "",
            }
            for item in soup.findAll("item")[:10]
        ]

    return _with_retry(_fetch, retries=3, base_delay=1.0) or []


def get_smart_news(ticker, info=None, is_us=False):
    raw_headlines = []
    company_name = ticker
    country = "USA"
    if info:
        company_name = (
            info.get('shortName') or info.get('longName') or ticker
        ).replace(" Ltd.", "").replace(" Inc.", "").replace(" Corp.", "").strip()
        currency = info.get('currency', 'USD')
        if currency in GLOBAL_MACRO:
            country = GLOBAL_MACRO[currency].get('country', 'USA')
    if not is_us:
        raw_headlines = fetch_google_news_institutional(company_name, country)
        if not raw_headlines:
            try:
                yf_n = _with_retry(lambda: yf.Ticker(ticker).news)
                if yf_n:
                    raw_headlines.extend(yf_n)
            except Exception:
                pass
    else:
        try:
            _, f_news = FinvizService.get_stock_data(ticker)
            if f_news:
                raw_headlines.extend(f_news)
        except Exception:
            pass
        if len(raw_headlines) < 5:
            try:
                yf_n = _with_retry(lambda: yf.Ticker(ticker).news)
                if yf_n:
                    raw_headlines.extend(yf_n)
            except Exception:
                pass
        if len(raw_headlines) < 3:
            raw_headlines.extend(fetch_google_news_institutional(company_name, "USA"))

    processed, seen = [], set()
    for h in raw_headlines:
        key = h.get('link') or h.get('title', '')
        if key in seen:
            continue
        seen.add(key)
        processed.append(analyze_headline_institutional(h))

    if not processed:
        return {"score": 50, "short_term": 50, "medium_term": 50, "event_risk": "Low"}, []

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


def get_robust_peers(symbol, target_sector, target_industry, target_mkt_cap, is_us=False):
    """
    Returns a dict with two peer buckets:
      'cat_a': Direct comparables — same sector AND same industry
      'cat_b': Scale benchmarks   — same sector, different industry, similar market cap

    Strategy:
      1. Finviz screener (US only, industry-filtered)
      2. yahooquery recommendations
      3. Batch fetch → strict sector filter → split by industry match
    """
    exchange_suffix = ("." + symbol.split(".")[-1]) if "." in symbol else ""
    raw_candidates: list[str] = []

    # Source 1: Finviz (US stocks, industry-level)
    if is_us:
        try:
            fv = FinvizService.get_peers(target_sector, target_industry, str(target_mkt_cap)) or []
            raw_candidates.extend(fv)
        except Exception:
            pass

    # Source 2: yahooquery recommendations (works for international)
    try:
        t = YQTicker(symbol)
        recs = _with_retry(lambda: t.recommendations, retries=2)
        if recs is not None and symbol in recs:
            rec_syms = [i['symbol'] for i in recs[symbol].get('recommendedSymbols', [])]
            raw_candidates.extend(rec_syms)
    except Exception:
        pass

    # Source 3: yfinance similar securities lookup (for international tickers)
    if not is_us and len(raw_candidates) < 5:
        try:
            # Try fetching recommendations from yfinance (newer versions expose this)
            yf_stock = yf.Ticker(symbol)
            yf_recs = getattr(yf_stock, 'recommendations', None)
            if yf_recs is not None and not yf_recs.empty:
                # Collect any related tickers surfaced
                if 'To Grade' in yf_recs.columns:
                    pass  # This is analyst grades, not peer symbols
        except Exception:
            pass

    # Deduplicate; remove self
    seen = set()
    deduped = []
    for c in raw_candidates:
        if c != symbol and c not in seen:
            seen.add(c)
            deduped.append(c)

    if not deduped:
        return {"cat_a": [], "cat_b": []}

    cat_a: list[str] = []  # same sector + same industry
    cat_b: list[str] = []  # same sector, different industry

    batch = deduped[:20]
    try:
        objs = _with_retry(lambda: yf.Tickers(" ".join(batch)), retries=2)
        if objs is None:
            # Fallback: return first N as cat_a without filtering
            return {"cat_a": deduped[:6], "cat_b": []}

        for p in batch:
            try:
                p_inf = objs.tickers[p].info or {}
                if not p_inf:
                    continue
                p_sector = p_inf.get('sector')
                p_industry = p_inf.get('industry')
                p_cap = _safe_float(p_inf.get('marketCap'), 0) or 0

                # Hard filter: sector must match
                if p_sector != target_sector:
                    continue

                # Soft market-cap filter — Category A: 0.1x – 10x, Category B: 0.2x – 5x
                if target_mkt_cap and p_cap:
                    if p_cap < target_mkt_cap * 0.1 or p_cap > target_mkt_cap * 10:
                        continue

                if p_industry == target_industry:
                    # Category A: Direct comparables
                    cat_a.append(p)
                else:
                    # Category B: Scale benchmarks (tighter cap band)
                    if not (target_mkt_cap and p_cap) or (
                        p_cap >= target_mkt_cap * 0.2 and p_cap <= target_mkt_cap * 5
                    ):
                        cat_b.append(p)
            except Exception:
                pass
    except Exception:
        # Total batch fetch failure: classify all as cat_a
        return {"cat_a": deduped[:6], "cat_b": []}

    # If Cat A is empty (rare industry), promote some Cat B as Cat A
    if not cat_a and cat_b:
        cat_a = cat_b[:4]
        cat_b = cat_b[4:]

    return {"cat_a": cat_a[:8], "cat_b": cat_b[:6]}


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
