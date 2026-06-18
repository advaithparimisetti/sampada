# backend/config.py
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env = Path(__file__).parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except Exception:
    pass

# --- API KEYS ---
# Get a free key from: https://www.alphavantage.co/support/#api-key
ALPHA_VANTAGE_KEY = "6Q03FNB4SNG594R3" 

# --- CACHE SETTINGS ---
CACHE = {}
CACHE_DURATION = 900 

# --- SOURCES ---
MAJOR_SOURCES = ['Reuters', 'Bloomberg', 'CNBC', 'WSJ', 'Yahoo Finance', 'MarketWatch', 'Financial Times']

# --- GLOBAL MACRO MAP ---
# Maps Currency -> {Risk Free Ticker, Equity Risk Premium (ERP), Country}
GLOBAL_MACRO = {
    'USD': {'rf_ticker': '^TNX', 'erp': 0.055, 'country': 'USA'},       
    'EUR': {'rf_ticker': '^GDAXI', 'erp': 0.060, 'country': 'Europe'},  
    'INR': {'rf_ticker': '^IGBV', 'erp': 0.072, 'country': 'India'},    
    'GBP': {'rf_ticker': '^FTSE', 'erp': 0.065, 'country': 'UK'},       
    'JPY': {'rf_ticker': '^N225', 'erp': 0.065, 'country': 'Japan'},    
    'CAD': {'rf_ticker': '^GSPTSE', 'erp': 0.055, 'country': 'Canada'}, 
    'AUD': {'rf_ticker': '^AXJO', 'erp': 0.055, 'country': 'Australia'},
    'CNY': {'rf_ticker': '000001.SS', 'erp': 0.065, 'country': 'China'},
    'HKD': {'rf_ticker': '^HSI', 'erp': 0.060, 'country': 'Hong Kong'}, 
    'SGD': {'rf_ticker': '^STI', 'erp': 0.055, 'country': 'Singapore'}, 
}

# --- DISCLAIMER (INSTITUTIONAL GRADE) ---
LEGAL_DISCLAIMER = """
STRICTLY PRIVATE & CONFIDENTIAL
DISCLAIMER: EDUCATIONAL SIMULATION ONLY - NOT FINANCIAL ADVICE

This material is a computer-generated analytical simulation for educational purposes only. 
It does not constitute investment advice, a recommendation, or an offer to buy or sell securities. 
No reliance should be placed on the outputs. All data is estimated.
"""