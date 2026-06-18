# backend/models.py
from pydantic import BaseModel
from typing import List, Any, Dict, Optional


class PeerData(BaseModel):
    symbol: str
    price: float
    mkt_cap: str
    pe: Any
    ev_ebitda: Any
    ev_sales: Any
    net_debt_ebitda: Any
    roic: str
    similarity: float
    category: Optional[str] = "A"    # 'A' = direct comparables, 'B' = scale benchmarks


class ExportRequest(BaseModel):
    ticker: str
    target_name: str
    verdict: str
    implied_price: float
    current_price: float = 0.0
    upside: float
    summary: Optional[str] = None
    peers: List[PeerData]
    currency_symbol: str
    dcf_range: str
    deal_type: str
    view_mode: str = "Internal"          # "Internal" | "Client"
    swot: Dict[str, List[str]]
    wacc_components: Dict[str, Any]
    tear_sheet_data: Dict[str, Any]
    market_data: Dict[str, Any]
    valuation_analysis: Optional[Dict[str, Any]] = None
    financials: Optional[Dict[str, Any]] = None
    football_field: Optional[Dict[str, Any]] = None
    theses: Optional[Dict[str, str]] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
