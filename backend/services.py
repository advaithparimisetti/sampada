# backend/services.py
import requests
import yfinance as yf
from finvizfinance.quote import finvizfinance
from finvizfinance.screener.overview import Overview
from config import ALPHA_VANTAGE_KEY

# ---------------------------------------------------------------------------
# COMMODITY & MACRO SERVICE  — primary source: yfinance futures (free, keyless)
# Alpha Vantage is kept only as a CPI inflation fallback.
# ---------------------------------------------------------------------------

# yfinance futures tickers for each commodity
_YF_COMMODITY_MAP = {
    "WTI":         "CL=F",   # WTI Crude Oil  ($/bbl)
    "BRENT":       "BZ=F",   # Brent Crude    ($/bbl)
    "GOLD":        "GC=F",   # Gold Spot      ($/oz)
    "NATURAL_GAS": "NG=F",   # Henry Hub Nat Gas ($/MMBtu)
    "ALUMINUM":    "ALI=F",  # Aluminum Futures
}


def _yf_spot(ticker_sym: str):
    """Return the latest closing price for a yfinance ticker, or None."""
    try:
        hist = yf.Ticker(ticker_sym).history(period="2d")
        if hist is not None and not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return None


class AlphaVantageService:
    BASE_URL = "https://www.alphavantage.co/query"

    @staticmethod
    def get_data(function, **kwargs):
        params = {"function": function, "apikey": ALPHA_VANTAGE_KEY}
        params.update(kwargs)
        try:
            r = requests.get(AlphaVantageService.BASE_URL, params=params, timeout=6)
            return r.json()
        except Exception:
            return None

    @staticmethod
    def get_macro_indicators():
        """
        Returns risk_free_rate and inflation.
        Source priority:
          1. yfinance ^TNX  → 10Y US Treasury yield (real-time, free)
          2. Alpha Vantage TREASURY_YIELD → fallback
          3. Alpha Vantage CPI            → YoY inflation (best effort)
          4. Hard-coded anchors if everything fails
        """
        data = {"risk_free_rate": 0.042, "inflation": 0.031}

        # 10Y Treasury yield via yfinance (^TNX quotes in percentage points)
        rf = _yf_spot("^TNX")
        if rf and rf > 0:
            data["risk_free_rate"] = round(rf / 100.0, 4)
        else:
            try:
                res = AlphaVantageService.get_data("TREASURY_YIELD", interval="monthly", maturity="10year")
                if res and "data" in res and res["data"]:
                    val = float(res["data"][0]["value"])
                    if val > 0:
                        data["risk_free_rate"] = round(val / 100.0, 4)
            except Exception:
                pass

        # YoY CPI inflation via Alpha Vantage (best effort; not critical path)
        try:
            res = AlphaVantageService.get_data("CPI", interval="monthly")
            if res and "data" in res and len(res["data"]) > 12:
                curr = float(res["data"][0]["value"])
                prev = float(res["data"][12]["value"])
                if prev > 0:
                    data["inflation"] = round((curr - prev) / prev, 4)
        except Exception:
            pass

        return data

    @staticmethod
    def get_commodities():
        """
        Fetches live commodity prices exclusively via yfinance futures tickers.
        No API key required. Returns numeric values or 'N/A'.
        """
        commodities = {}
        for name, sym in _YF_COMMODITY_MAP.items():
            price = _yf_spot(sym)
            commodities[name] = price if price is not None else "N/A"
        return commodities

class FinvizService:
    @staticmethod
    def get_stock_data(ticker):
        try:
            stock = finvizfinance(ticker)
            info = stock.ticker_fundament()
            news = stock.ticker_news()
            formatted_news = []
            if news is not None and not news.empty:
                for _, row in news.head(8).iterrows():
                    formatted_news.append({"title": row['Title'], "link": row['Link'], "publisher": "Finviz"})
            return info, formatted_news
        except: return None, None

    @staticmethod
    def get_peers(sector, industry, market_cap_str):
        try:
            foverview = Overview()
            filters_dict = {'Sector': sector, 'Industry': industry}
            foverview.set_filter(filters_dict=filters_dict)
            df = foverview.screener_view()
            if not df.empty:
                return df['Ticker'].head(8).tolist()
        except: pass
        return []