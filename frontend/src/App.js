/* src/App.js */
import React, { useState, useMemo, useEffect } from 'react';
import axios from 'axios';
import { RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer, Radar as RechartsRadar } from 'recharts';
import { onAuthStateChanged, signOut } from 'firebase/auth';
import { auth } from './firebase';
import { subscribeWatchlist, addToWatchlist, removeFromWatchlist } from './watchlist';
import './App.css';
import AuthPage from './AuthPage';
import { METHODOLOGY, InfoBtn, FootballField, ConsensusBar, MarketDataCard, HeartButton, ProfileModal } from './components';

const API = process.env.REACT_APP_API_URL || 'http://localhost:8000';

// ─── Ultra-expanded tear sheet schema ─────────────────────────────────────────
const INCOME_KEYS = ['Revenue', 'Gross Profit', 'Operating Income', 'EBITDA', 'Net Income', 'Gross Margin', 'Operating Margin', 'Net Margin'];
const BALANCE_KEYS = ['Total Assets', 'Total Liabilities', 'Total Equity', 'Total Debt', 'Cash', 'Working Capital'];
const CF_KEYS = ['Operating CF', 'Capex', 'Free Cash Flow', 'Levered FCF', 'Unlevered FCF', 'D&A', 'SBC'];

function App() {
  // ── Auth State ──────────────────────────────────────────────────────────────
  const [user, setUser] = useState(undefined); // undefined = loading

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
      setUser(firebaseUser || null);
    });
    return unsubscribe;
  }, []);

  // ── Analysis State ──────────────────────────────────────────────────────────
  const [ticker, setTicker] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [dealType, setDealType] = useState('M&A');
  const [viewMode, setViewMode] = useState('Internal');
  const [peerPercentile, setPeerPercentile] = useState(50);
  const [valuationPremium, setValuationPremium] = useState(0);
  const [thesisMode, setThesisMode] = useState('Bull');

  const [activeModal, setActiveModal] = useState(null);
  const [selectedPeer, setSelectedPeer] = useState(null);
  const [peerSummaryExpanded, setPeerSummaryExpanded] = useState(false);
  const [showPeerRaw, setShowPeerRaw] = useState(false);
  const [activeInfo, setActiveInfo] = useState(null);
  const [showFinancials, setShowFinancials] = useState(false);
  const [finTab, setFinTab] = useState('income');

  // ── Watchlist / Profile State ─────────────────────────────────────────────
  const [watchlist, setWatchlist] = useState([]);
  const [showProfile, setShowProfile] = useState(false);
  const [favBusy, setFavBusy] = useState(false);

  const [manualPeerTicker, setManualPeerTicker] = useState('');
  const [activePeers, setActivePeers] = useState([]);
  const [customPeersData, setCustomPeersData] = useState([]);
  const [peerSearchResults, setPeerSearchResults] = useState([]);
  const [peerSearchLoading, setPeerSearchLoading] = useState(false);
  const [peerSearchOpen, setPeerSearchOpen] = useState(false);

  // ── Watchlist live subscription ───────────────────────────────────────────
  useEffect(() => {
    if (!user || user.isGuest) { setWatchlist([]); return; }
    const unsub = subscribeWatchlist(user.uid, setWatchlist);
    return unsub;
  }, [user]);

  const isFavorited = useMemo(
    () => !!(data && watchlist.some((w) => w.ticker === (data.symbol || '').toUpperCase())),
    [data, watchlist]
  );

  const toggleFavorite = async () => {
    if (!data || !user || user.isGuest || favBusy) return;
    setFavBusy(true);
    try {
      if (isFavorited) {
        await removeFromWatchlist(user.uid, data.symbol);
      } else {
        await addToWatchlist(user.uid, {
          ticker: data.symbol,
          name: data.name,
          price: data.price,
          verdict: data.verdict,
          currency_symbol: data.currency_symbol,
        });
      }
    } catch (_) {
      alert('Could not update watchlist. Check your connection.');
    }
    setFavBusy(false);
  };

  // ── Fetch Analysis ──────────────────────────────────────────────────────────
  const fetchAnalysis = async (tickerOverride) => {
    const symbol = (typeof tickerOverride === 'string' ? tickerOverride : ticker).trim();
    if (!symbol) return;
    if (symbol !== ticker) setTicker(symbol);
    setShowProfile(false);
    setLoading(true);
    setError('');
    setActiveModal(null);
    setSelectedPeer(null);
    setShowFinancials(false);
    setCustomPeersData([]);
    setActiveInfo(null);
    try {
      setData(null);
      const headers = {};
      if (user && !user.isGuest) {
        try {
          const token = await user.getIdToken();
          headers['Authorization'] = `Bearer ${token}`;
        } catch (_) {}
      }
      const response = await axios.get(`${API}/api/analyze/${symbol}`, { headers });
      setData(response.data);
      if (response.data.peers) setActivePeers(response.data.peers.map((p) => p.symbol));
    } catch (err) {
      setError('Ticker not found. Try appending ".NS", ".L", or ".PA"');
      setData(null);
    }
    setLoading(false);
  };

  // ── PPT Export ──────────────────────────────────────────────────────────────
  const exportPPT = async () => {
    if (!data) return;
    try {
      const payload = {
        ticker: data.symbol,
        target_name: data.name,
        verdict: data.verdict,
        implied_price: Number(impliedValuation.finalPrice),
        upside: Number(impliedValuation.upside),
        summary: data.summary,
        peers: [...data.peers, ...customPeersData].filter((p) => activePeers.includes(p.symbol)),
        currency_symbol: data.currency_symbol,
        dcf_range: data.valuation_analysis.dcf_range,
        deal_type: dealType,
        view_mode: viewMode,
        swot: data.swot,
        wacc_components: data.wacc_components,
        tear_sheet_data: data.tear_sheet_data,
        market_data: data.market_data,
        valuation_analysis: data.valuation_analysis,
        financials: data.financials,
        current_price: data.price,
        football_field: data.football_field,
        theses: data.theses,
        sector: data.sector,
        industry: data.industry,
      };
      const response = await axios.post(`${API}/api/export_ppt`, payload, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `SAMPADA_${data.symbol}_${viewMode}_${new Date().toISOString().slice(0, 10)}.pptx`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (e) {
      const d = e?.response?.data;
      let msg = 'Export failed';
      if (d) {
        if (typeof d === 'string') msg = d;
        else if (d.detail) msg = d.detail;
      } else if (e?.message) msg = e.message;
      alert(msg);
    }
  };

  // ── Manual Peer ─────────────────────────────────────────────────────────────
  const addManualPeer = async (symbolOverride) => {
    const sym = (symbolOverride || manualPeerTicker || '').trim().toUpperCase();
    if (!sym || !data) return;
    try {
      const symMap = { $: 'USD', '£': 'GBP', '€': 'EUR', '₹': 'INR', '¥': 'JPY', 'C$': 'CAD', 'A$': 'AUD', 'HK$': 'HKD' };
      const baseCurrency = symMap[data.currency_symbol.trim()] || 'USD';
      const params = new URLSearchParams({ ticker: sym, base_currency: baseCurrency });
      const res = await axios.get(`${API}/api/peer_info?${params.toString()}`);
      const newPeer = res.data;
      const allExisting = [...data.peers, ...customPeersData];
      if (!allExisting.find((p) => p.symbol === newPeer.symbol)) {
        setCustomPeersData([...customPeersData, newPeer]);
        setActivePeers([...activePeers, newPeer.symbol]);
      }
      setManualPeerTicker('');
      setPeerSearchResults([]);
      setPeerSearchOpen(false);
    } catch (_) {
      alert('Could not find peer. Try the exact ticker symbol (e.g. AAPL, AIR.PA).');
    }
  };

  // Debounced ticker search for peer combobox
  const _peerSearchRef = React.useRef(null);
  const handlePeerSearchInput = (val) => {
    setManualPeerTicker(val);
    setPeerSearchOpen(true);
    if (_peerSearchRef.current) clearTimeout(_peerSearchRef.current);
    if (!val || val.length < 1) { setPeerSearchResults([]); return; }
    _peerSearchRef.current = setTimeout(async () => {
      setPeerSearchLoading(true);
      try {
        const res = await axios.get(`${API}/api/ticker_search?q=${encodeURIComponent(val)}`);
        setPeerSearchResults(res.data.results || []);
      } catch (_) {
        setPeerSearchResults([]);
      }
      setPeerSearchLoading(false);
    }, 280);
  };

  const togglePeer = (symbol) => {
    if (activePeers.includes(symbol)) setActivePeers(activePeers.filter((s) => s !== symbol));
    else setActivePeers([...activePeers, symbol]);
  };

  // ── Implied Valuation (memoized) ────────────────────────────────────────────
  const impliedValuation = useMemo(() => {
    if (!data) return { finalPrice: '0.00', upside: '0.0', count: 0, avgPe: 0, avgEv: 0, compsPrice: 0 };
    const allPeers = [...(data.peers || []), ...customPeersData];
    const activeList = allPeers.filter((p) => activePeers.includes(p.symbol));

    if (activeList.length === 0) {
      const dcfVal = data.valuation_analysis.dcf_price || 0;
      return {
        finalPrice: dcfVal.toFixed(2),
        upside: data.price ? (((dcfVal - data.price) / data.price) * 100).toFixed(1) : '0.0',
        count: 0, avgPe: 0, avgEv: 0, compsPrice: 0,
      };
    }

    const metricKey = dealType === 'IPO' ? 'ev_sales' : dealType === 'LBO' ? 'ev_ebitda' : 'pe';
    const validMultiples = activeList.map((p) => p[metricKey]).filter((v) => v !== '-' && v > 0);
    validMultiples.sort((a, b) => a - b);

    let selectedMultiple = 0;
    if (validMultiples.length > 0) {
      const idx = Math.floor((peerPercentile / 100) * (validMultiples.length - 1));
      selectedMultiple = validMultiples[idx];
    }

    let targetMetricVal = data.target_ratios.pe;
    if (dealType === 'IPO') targetMetricVal = data.target_ratios.ev_sales;
    if (dealType === 'LBO') targetMetricVal = data.target_ratios.ev_ebitda;

    let compsImpliedPrice = 0;
    if (selectedMultiple > 0 && targetMetricVal !== '-' && targetMetricVal > 0) {
      compsImpliedPrice = (data.price / targetMetricVal) * selectedMultiple;
    }
    compsImpliedPrice *= 1 + valuationPremium / 100;

    const dcfVal = data.valuation_analysis.dcf_price !== 0 ? data.valuation_analysis.dcf_price : compsImpliedPrice;
    const finalPrice = compsImpliedPrice * 0.4 + dcfVal * 0.6;
    const upside = data.price ? ((finalPrice - data.price) / data.price) * 100 : 0;

    const harmonicMean = (vals) => {
      const valid = vals.filter((v) => v > 0);
      if (!valid.length) return 0;
      return valid.length / valid.reduce((s, v) => s + 1 / v, 0);
    };

    return {
      finalPrice: finalPrice.toFixed(2),
      upside: upside.toFixed(1),
      multipleUsed: selectedMultiple,
      count: activeList.length,
      avgPe: harmonicMean(activeList.map((p) => (typeof p.pe === 'number' ? p.pe : 0))),
      avgEv: harmonicMean(activeList.map((p) => (typeof p.ev_ebitda === 'number' ? p.ev_ebitda : 0))),
      compsPrice: compsImpliedPrice.toFixed(2),
    };
  }, [data, activePeers, customPeersData, dealType, peerPercentile, valuationPremium]);

  // ── Helpers ─────────────────────────────────────────────────────────────────
  const getColor = (val, type) => {
    if (type === 'sentiment') return val > 0 ? '#00ff00' : val < 0 ? '#ff4444' : '#888';
    if (type === 'verdict') return val?.includes('POSITIVE') ? '#00ff00' : val?.includes('NEGATIVE') ? '#ff4444' : '#ffa500';
    if (type === 'similarity') return val >= 80 ? '#00ff00' : val >= 50 ? '#ffa500' : '#ff4444';
    return '#fff';
  };

  const formatMarketCap = (s) => (!s || s === 'N/A' ? 'N/A' : s);

  const getPeerSummary = (p) =>
    p?.summary || p?.longBusinessSummary || p?.shortBusinessSummary || '';

  const getShortInfoLine = (p) => {
    if (!p) return '';
    return [p.sector, p.industry, p.mkt_cap].filter(Boolean).join(' • ');
  };

  const firstSentence = (txt) => {
    if (!txt) return '';
    const s = txt.replace(/\n/g, ' ').split(/\. |\n/)[0] || txt;
    return s.length > 200 ? s.slice(0, 197) + '...' : s.trim();
  };

  const currentPeerSummary = selectedPeer ? getPeerSummary(selectedPeer) : '';
  const shortInfoLine = selectedPeer ? getShortInfoLine(selectedPeer) : '';
  const shortSummaryFirst = firstSentence(currentPeerSummary);

  const displayVerdict =
    viewMode === 'Client' && data?.verdict?.includes('NEGATIVE') ? 'REVIEW' : data?.verdict;

  // ── Loading state (waiting for Firebase to resolve) ─────────────────────────
  if (user === undefined) {
    return (
      <div style={{ minHeight: '100vh', background: '#050505', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#334', letterSpacing: '3px', fontFamily: 'JetBrains Mono, monospace' }}>
        LOADING...
      </div>
    );
  }

  // ── Auth gate ────────────────────────────────────────────────────────────────
  if (!user) {
    return <AuthPage onAuth={(u) => setUser(u)} />;
  }

  // ── Main Dashboard ────────────────────────────────────────────────────────────
  return (
    <>
      <div className="blob" />
      <div className={`search-container ${data ? 'search-top' : 'search-center'}`}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h1 className="brand-title">SAMPA<span style={{ color: '#00d4ff' }}>DA</span></h1>
          {data && (
            <div className="ticker-chip">
              <span className="ticker-chip-sym">{data.symbol}</span>
              <span className="ticker-chip-name">{data.name}</span>
              {user && !user.isGuest && (
                <HeartButton active={isFavorited} busy={favBusy} onClick={toggleFavorite} />
              )}
            </div>
          )}
          {user && (
            <button
              className="profile-btn"
              onClick={() => setShowProfile(true)}
              title="Profile & Watchlist"
            >
              <span className="profile-avatar">{(user.email || 'U').charAt(0).toUpperCase()}</span>
            </button>
          )}
        </div>

        {data && (
          <div className="switch-group" style={{ marginLeft: '40px', marginRight: 'auto' }}>
            <div className="switch-mode">
              {['IPO', 'M&A', 'LBO'].map((t) => (
                <div key={t} className={`mode-opt ${dealType === t ? 'active' : ''}`} onClick={() => setDealType(t)}>{t}</div>
              ))}
            </div>
            <div className="switch-mode">
              {['Internal', 'Client'].map((m) => (
                <div key={m} className={`mode-opt ${viewMode === m ? 'active' : ''}`} onClick={() => setViewMode(m)}>{m.toUpperCase()}</div>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
          <div className="input-wrapper">
            <input
              className="search-input"
              placeholder="ENTER TICKER..."
              value={ticker}
              onChange={(e) => { setTicker(e.target.value); setError(''); }}
              onKeyPress={(e) => e.key === 'Enter' && fetchAnalysis()}
            />
            <button className="search-btn" onClick={fetchAnalysis}>➜</button>
          </div>
          {data && <button className="btn-ppt" onClick={exportPPT}>EXPORT {viewMode.toUpperCase()} DECK</button>}
        </div>
        {error && <div className="error-msg" style={{ position: 'absolute', bottom: '-30px' }}>{error}</div>}
      </div>

      {loading && (
        <div style={{ position: 'absolute', top: '65%', width: '100%', textAlign: 'center', color: '#666', letterSpacing: '3px', fontSize: '0.9rem' }}>
          RUNNING MONTE CARLO SIMULATION...
        </div>
      )}

      {data && (
        <div className="dashboard">
          {/* Football Field */}
          <div className="card col-span-2">
            <span className="card-label">VALUATION FOOTBALL FIELD</span>
            <div style={{ marginTop: '25px' }}>
              <FootballField ranges={data.football_field} currentPrice={data.price} />
            </div>
          </div>

          {/* AI Assessment */}
          <div className="card" onClick={() => setActiveModal('verdict')} style={{ borderTop: `4px solid ${getColor(displayVerdict, 'verdict')}` }}>
            <span className="card-label">AI ASSESSMENT</span>
            <div className="confidence-badge" style={{ fontSize: '0.7rem' }}>
              MODEL ROBUSTNESS: <span style={{ color: getColor(data.confidence_score, 'confidence') }}>{data.confidence_score}%</span>
            </div>
            <h1 style={{ fontSize: (displayVerdict || '').length > 8 ? '2.5rem' : '4rem', fontWeight: 800, margin: 0, color: getColor(displayVerdict, 'verdict') }}>
              {displayVerdict}
            </h1>
            <div className="thesis-toggle">
              <div className={`thesis-btn ${thesisMode === 'Bull' ? 'bull' : ''}`} onClick={(e) => { e.stopPropagation(); setThesisMode('Bull'); }}>BULL CASE</div>
              <div className={`thesis-btn ${thesisMode === 'Bear' ? 'bear' : ''}`} onClick={(e) => { e.stopPropagation(); setThesisMode('Bear'); }}>BEAR CASE</div>
            </div>
            <p style={{ color: '#aaa', marginTop: '10px', fontSize: '0.8rem', lineHeight: '1.4', height: '40px', overflow: 'hidden' }}>
              {thesisMode === 'Bull' ? data.theses.bull : data.theses.bear}
            </p>
          </div>

          {/* Quality Scorecard */}
          <div className="card">
            <span className="card-label">QUALITY SCORECARD</span>
            <div className="scorecard-chart">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="65%" data={data.quality_scores}>
                  <PolarGrid stroke="#333" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#888', fontSize: 9 }} />
                  <RechartsRadar name="Score" dataKey="A" stroke="#00d4ff" strokeWidth={2} fill="#00d4ff" fillOpacity={0.2} />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Modeled Value */}
          <div className="card card-centered" onClick={() => setActiveModal('methods')}>
            <span className="card-label">MODELED VALUE</span>
            <div className="modeled-inner" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '3rem', fontFamily: 'JetBrains Mono', fontWeight: 'bold', letterSpacing: '-2px' }}>
                <span style={{ fontSize: '1.5rem', color: '#444', marginRight: '5px', verticalAlign: 'middle' }}>{data.currency_symbol}</span>
                {impliedValuation.finalPrice}
              </div>
              <div style={{ marginTop: '5px' }}>
                <div style={{ fontSize: '1rem', color: impliedValuation.upside > 0 ? '#00ff00' : '#ff4444', fontWeight: 'bold' }}>
                  {impliedValuation.upside}% Spread
                </div>
              </div>
              {(data.valuation_analysis?.blend_dcf != null) && (
                <div className="blend-pill" title="Dynamic, sector-aware blend of intrinsic (DCF) and relative (Comps) valuation">
                  BLEND · DCF {data.valuation_analysis.blend_dcf}% / COMPS {data.valuation_analysis.blend_comps}%
                </div>
              )}
              {data.backtest?.data_available && (
                <div style={{ marginTop: '12px', fontSize: '0.62rem', color: '#778', lineHeight: 1.5 }}>
                  <span style={{ color: '#00d4ff' }}>VALIDATION</span> · Fair value sits {data.backtest.mape}% from the
                  realized 12-mo price path
                  {data.backtest.outperformance != null && (
                    <> · {data.backtest.outperformance >= 0 ? 'beats' : 'trails'} street by {Math.abs(data.backtest.outperformance)}%</>
                  )}
                </div>
              )}
              <div style={{ marginTop: '18px' }}>
                <button onClick={(e) => { e.stopPropagation(); setShowFinancials(true); setFinTab('income'); }} className="btn-fin">
                  TEAR SHEET
                </button>
              </div>
            </div>
          </div>

          {/* Street View */}
          <div className="card" onClick={() => setActiveModal('news')}>
            <span className="card-label">STREET VIEW</span>
            <div style={{ marginTop: '30px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: '#ccc' }}>
                <span>ANALYST CONSENSUS</span>
                <span style={{ color: data.consensus?.data_available ? '#00d4ff' : '#556' }}>
                  {data.consensus?.recommendation || (data.consensus?.data_available === false ? 'COVERAGE PAUSED' : 'N/A')}
                </span>
              </div>
              {data.consensus?.data_available === false ? (
                <div style={{ padding: '10px 0', color: '#445', fontSize: '0.72rem', fontStyle: 'italic' }}>
                  Insufficient consensus data for this listing. No analyst coverage found.
                </div>
              ) : (
                <>
                  <ConsensusBar consensus={data.consensus} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: '#666', marginTop: '5px' }}>
                    <span>Buy: {data.consensus?.buy ?? '–'}</span>
                    <span>Hold: {data.consensus?.hold ?? '–'}</span>
                    <span>Sell: {data.consensus?.sell ?? '–'}</span>
                  </div>
                </>
              )}
              <div style={{ borderTop: '1px solid #333', marginTop: '20px', paddingTop: '15px' }}>
                <div style={{ fontSize: '0.8rem', color: '#ccc' }}>NEWS SENTIMENT</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: getColor(data.sentiment_score, 'sentiment') }}>
                  {data.sentiment_score} <span style={{ fontSize: '0.8rem', color: '#666' }}>/ 100</span>
                </div>
              </div>
            </div>
          </div>

          {/* Peer Strategy */}
          <div className="card col-span-3" onClick={() => setActiveModal('peers')} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <span className="card-label" style={{ position: 'static' }}>PEER STRATEGY</span>
              <h2 style={{ marginTop: '10px', marginBottom: '5px', fontSize: '1.5rem', color: '#fff' }}>COMPARABLE ANALYSIS</h2>
              <p style={{ color: '#666', fontSize: '0.8rem' }}>Click to manage peer group and multiples.</p>
            </div>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-end', height: '60px' }}>
              {[30, 50, 100, 60, 40].map((h, i) => (
                <div key={i} style={{ width: '15px', height: `${h}%`, background: i === 2 ? '#00d4ff' : '#333', borderRadius: '2px', boxShadow: i === 2 ? '0 0 15px rgba(0,212,255,0.4)' : 'none' }} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── ULTRA-EXPANDED TEAR SHEET MODAL ─────────────────────────────────── */}
      {showFinancials && data?.financials && (
        <div className="modal-overlay" onClick={() => setShowFinancials(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '1100px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px' }}>
              <h2 style={{ margin: 0, color: '#00d4ff' }}>FINANCIAL TEAR SHEET — {data.symbol}</h2>
              <button onClick={() => setShowFinancials(false)} style={{ background: 'none', border: 'none', color: '#fff', fontSize: '1.5rem', cursor: 'pointer' }}>✕</button>
            </div>

            {/* Tab bar */}
            <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
              {[['income', 'INCOME STATEMENT'], ['balance', 'BALANCE SHEET'], ['cashflow', 'CASH FLOW']].map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setFinTab(key)}
                  style={{
                    padding: '8px 18px',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    fontSize: '0.72rem',
                    fontWeight: 700,
                    letterSpacing: '1px',
                    background: finTab === key ? '#00d4ff' : '#1a1a2a',
                    color: finTab === key ? '#000' : '#666',
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Income Statement */}
            {finTab === 'income' && data.financials.income?.length > 0 && (
              <FinTable
                rows={INCOME_KEYS}
                data={data.financials.income}
                isMargin={(k) => k.toLowerCase().includes('margin')}
              />
            )}

            {/* Balance Sheet */}
            {finTab === 'balance' && data.financials.balance?.length > 0 && (
              <FinTable rows={BALANCE_KEYS} data={data.financials.balance} />
            )}

            {/* Cash Flow */}
            {finTab === 'cashflow' && data.financials.cashflow?.length > 0 && (
              <FinTable rows={CF_KEYS} data={data.financials.cashflow} />
            )}

            {/* No data fallback */}
            {((finTab === 'income' && !data.financials.income?.length) ||
              (finTab === 'balance' && !data.financials.balance?.length) ||
              (finTab === 'cashflow' && !data.financials.cashflow?.length)) && (
              <div style={{ color: '#444', fontStyle: 'italic', padding: '30px 0', textAlign: 'center' }}>
                No financial data available for this period.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── MODALS ──────────────────────────────────────────────────────────── */}
      {activeModal && (
        <div className="modal-overlay" onClick={() => { setActiveModal(null); setSelectedPeer(null); setActiveInfo(null); }}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
              <h2 style={{ margin: 0, color: '#00d4ff', fontSize: '1.2rem' }}>
                {activeModal === 'verdict' ? 'ASSESSMENT' : activeModal.toUpperCase().replace('_', ' ')}
              </h2>
              <button onClick={() => { setActiveModal(null); setSelectedPeer(null); }} style={{ background: 'none', border: 'none', color: '#666', fontSize: '1.5rem', cursor: 'pointer' }}>✕</button>
            </div>

            {/* Verdict Modal */}
            {activeModal === 'verdict' && (
              <div>
                <h1 style={{ color: getColor(displayVerdict, 'verdict'), fontSize: '3rem', marginTop: 0 }}>
                  ASSESSMENT: {displayVerdict}
                </h1>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                  <div style={{ padding: '20px', background: '#111', borderRadius: '15px', borderLeft: '4px solid #0f0' }}>
                    <h3 style={{ color: '#0f0', marginTop: 0 }}>THE BULL CASE</h3>
                    <p style={{ color: '#ddd', fontSize: '0.9rem' }}>{data.theses.bull}</p>
                  </div>
                  <div style={{ padding: '20px', background: '#111', borderRadius: '15px', borderLeft: '4px solid #f00' }}>
                    <h3 style={{ color: '#f00', marginTop: 0 }}>THE BEAR CASE</h3>
                    <p style={{ color: '#ddd', fontSize: '0.9rem' }}>{data.theses.bear}</p>
                  </div>
                </div>
                {viewMode === 'Internal' && (
                  <div style={{ marginTop: '20px', padding: '15px', background: '#1a0808', borderRadius: '10px', borderLeft: '4px solid #ff4444' }}>
                    <div style={{ fontSize: '0.75rem', color: '#ff6666', fontWeight: 'bold', marginBottom: '8px' }}>STRESS SCENARIO (INTERNAL ONLY)</div>
                    <div style={{ color: '#aaa', fontSize: '0.85rem' }}>
                      DCF Range: {data.valuation_analysis?.dcf_range}  |  WACC: {data.valuation_analysis?.wacc}%  |  Growth: {data.valuation_analysis?.growth_assumed}%
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Methods Modal */}
            {activeModal === 'methods' && (
              <div style={{ position: 'relative' }}>
                {activeInfo && (
                  <div className="info-modal" onClick={() => setActiveInfo(null)}>
                    <div style={{ color: '#00d4ff', fontWeight: 'bold', marginBottom: '10px' }}>{activeInfo.title}</div>
                    <div style={{ color: '#ccc', fontSize: '0.85rem', lineHeight: '1.6' }}>{activeInfo.text}</div>
                  </div>
                )}
                <div style={{ marginBottom: '30px', padding: '20px', background: '#1a1a1a', borderRadius: '15px', border: '1px solid #333' }}>
                  <h3 style={{ color: '#00d4ff', fontSize: '1rem', marginTop: 0 }}>BUSINESS PROFILE</h3>
                  <p style={{ color: '#ccc', fontSize: '0.9rem', lineHeight: '1.6', maxHeight: '150px', overflowY: 'auto' }}>
                    {data.summary || 'No description available for this company.'}
                  </p>
                </div>
                <div style={{ marginBottom: '30px', background: '#1a1a1a', padding: '20px', borderRadius: '15px', border: '1px solid #333' }}>
                  <div style={{ color: '#00d4ff', fontSize: '0.9rem', marginBottom: '15px', fontWeight: 'bold' }}>LIVE SENSITIVITY ANALYSIS</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '20px', marginBottom: '25px' }}>
                    <div className="val-box"><div className="val-label">DCF Value</div><div className="val-num" style={{ color: '#888' }}>{data.currency_symbol}{data.valuation_analysis.dcf_price}</div></div>
                    <div className="val-box"><div className="val-label">Comps Value (Dynamic)</div><div className="val-num" style={{ color: '#00d4ff' }}>{data.currency_symbol}{impliedValuation.compsPrice}</div></div>
                    <div className="val-box" style={{ borderColor: '#00d4ff' }}><div className="val-label" style={{ color: '#fff' }}>Final Blended</div><div className="val-num" style={{ color: '#00ff00' }}>{data.currency_symbol}{impliedValuation.finalPrice}</div></div>
                  </div>
                  <div style={{ display: 'flex', gap: '40px' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', color: '#888', marginBottom: '5px' }}><span>PEER PERCENTILE</span><span>{peerPercentile}th</span></div>
                      <input type="range" min="0" max="100" value={peerPercentile} onChange={(e) => setPeerPercentile(e.target.value)} className="slider" />
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', color: '#888', marginBottom: '5px' }}><span>PREMIUM</span><span>{valuationPremium}%</span></div>
                      <input type="range" min="-30" max="30" value={valuationPremium} onChange={(e) => setValuationPremium(e.target.value)} className="slider" />
                    </div>
                  </div>
                </div>
                {data.backtest?.data_available && (
                  <div className="validation-card" style={{ marginBottom: '30px' }}>
                    <div style={{ color: '#00d4ff', fontSize: '0.9rem', marginBottom: '14px', fontWeight: 'bold' }}>
                      MODEL VALIDATION — HISTORICAL ACCURACY
                    </div>
                    <div className="validation-row">
                      <div className="validation-metric">
                        <div className="v-num" style={{ color: '#fff' }}>{data.backtest.mape}%</div>
                        <div className="v-lbl">SAMPADA MAPE</div>
                      </div>
                      <div className="validation-metric">
                        <div className="v-num" style={{ color: '#aaa' }}>{data.backtest.consensus_mape != null ? data.backtest.consensus_mape + '%' : '—'}</div>
                        <div className="v-lbl">Street MAPE</div>
                      </div>
                      <div className="validation-metric">
                        <div className="v-num" style={{ color: (data.backtest.outperformance ?? 0) >= 0 ? '#00e576' : '#ff6666' }}>
                          {data.backtest.outperformance != null ? (data.backtest.outperformance >= 0 ? '+' : '') + data.backtest.outperformance + '%' : '—'}
                        </div>
                        <div className="v-lbl">vs Street</div>
                      </div>
                      <div className="validation-metric">
                        <div className="v-num" style={{ color: '#aaa' }}>{data.backtest.hit_ratio != null ? data.backtest.hit_ratio + '%' : '—'}</div>
                        <div className="v-lbl">Range Hit ({data.backtest.window_months}mo)</div>
                      </div>
                    </div>
                    <div className="validation-note">
                      {data.backtest.methodology}
                    </div>
                  </div>
                )}
                {data.valuation_analysis?.fcf_lookback && (
                  <div style={{ fontSize: '0.72rem', color: '#778', marginBottom: '18px' }}>
                    FCF normalized over a <b style={{ color: '#00d4ff' }}>{data.valuation_analysis.fcf_lookback}-year</b> window (sector-adjusted for cyclicality);
                    blend dynamically weighted <b style={{ color: '#00d4ff' }}>{data.valuation_analysis.blend_dcf}% DCF / {data.valuation_analysis.blend_comps}% Comps</b>.
                  </div>
                )}
                <h3 style={{ fontSize: '1.2rem', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px', marginTop: '30px', color: '#fff' }}>VALUATION MATRIX & METHODOLOGIES</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '30px' }}>
                  {['dcf', 'wacc', 'growth', 'comps', 'range', 'blended'].map((key) => (
                    <div key={key} style={{ background: '#1a1a1a', padding: '20px', borderRadius: '10px', border: '1px solid #333' }}>
                      <div style={{ color: '#00d4ff', fontWeight: 'bold', fontSize: '0.9rem', marginBottom: '10px', display: 'flex', alignItems: 'center' }}>
                        <InfoBtn id={key} setActiveInfo={setActiveInfo} /> {METHODOLOGY[key].title}
                      </div>
                      <div style={{ color: '#aaa', fontSize: '0.8rem', lineHeight: '1.6' }}>{METHODOLOGY[key].text}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* News Modal */}
            {activeModal === 'news' && (
              <div style={{ padding: '20px' }}>
                {data.market_data && <MarketDataCard data={data.market_data} />}
                <div style={{ marginBottom: '25px', padding: '20px', background: '#1a1a1a', borderRadius: '10px', borderLeft: '4px solid #00d4ff' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ marginTop: 0, marginBottom: 0, fontSize: '1rem', color: '#fff' }}>SENTIMENT HORIZON</h3>
                    {data.sentiment_analysis?.nlp_engine && (
                      <span style={{ fontSize: '0.62rem', color: '#667', border: '1px solid #233', borderRadius: '20px', padding: '3px 10px' }}>
                        NLP · {data.sentiment_analysis.nlp_engine}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '15px' }}>
                    {[['SHORT TERM (3D)', data.sentiment_analysis?.short_term], ['MEDIUM TERM (30D)', data.sentiment_analysis?.medium_term], ['EVENT RISK', data.sentiment_analysis?.event_risk]].map(([label, val]) => (
                      <div key={label} style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: '0.7rem', color: '#888' }}>{label}</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: label === 'EVENT RISK' ? (val === 'High' ? '#ff4444' : val === 'Medium' ? '#ffa500' : '#00ff00') : getColor(val, 'sentiment') }}>
                          {val || '-'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div style={{ marginBottom: '30px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <h3 style={{ color: '#fff', fontSize: '1rem', margin: 0 }}>ANALYST CONSENSUS</h3>
                    <span style={{ color: data.consensus?.data_available ? '#00d4ff' : '#556', fontSize: '0.8rem' }}>
                      {data.consensus?.recommendation || (data.consensus?.data_available === false ? 'COVERAGE PAUSED' : 'N/A')}
                    </span>
                  </div>
                  {data.consensus?.data_available === false ? (
                    <div style={{ color: '#445', fontSize: '0.75rem', fontStyle: 'italic', padding: '6px 0' }}>
                      Insufficient consensus data — no analyst coverage found for this listing.
                    </div>
                  ) : (
                    <>
                      <ConsensusBar consensus={data.consensus} />
                      <div style={{ display: 'flex', gap: '15px', fontSize: '0.8rem', color: '#888', marginTop: '5px' }}>
                        <span>Buy: {data.consensus?.buy ?? '–'}</span>
                        <span>Hold: {data.consensus?.hold ?? '–'}</span>
                        <span>Sell: {data.consensus?.sell ?? '–'}</span>
                      </div>
                    </>
                  )}
                </div>
                <h3 style={{ marginTop: 0, color: '#fff', fontSize: '1rem', marginBottom: '20px' }}>LIVE NEWS WIRE</h3>
                {data.sentiment_analysis?.fallback === 'sector' && (data.headlines || []).length > 0 && (
                  <div className="news-fallback-note">
                    No company-specific coverage found — showing broader {data.industry || data.sector || 'sector'} news.
                  </div>
                )}
                <div className="news-wire">
                  {(data.headlines || []).length > 0
                    ? (data.headlines || []).map((h, i) => (
                        <div key={i} className="news-item">
                          <a href={h.link} target="_blank" rel="noreferrer" className="news-headline">{h.title}</a>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#666' }}><span>{h.publisher}</span></div>
                        </div>
                      ))
                    : <div style={{ color: '#666', fontStyle: 'italic' }}>No recent news found.</div>}
                </div>
              </div>
            )}

            {/* Peers Modal */}
            {activeModal === 'peers' && (
              <div>
                <div className="peers-header">
                  <div><div style={{ color: '#666', fontSize: '0.8rem' }}>SECTOR BENCHMARK</div><div style={{ fontSize: '1.2rem', color: '#fff', fontWeight: 'bold' }}>{data.name} vs. Peer Group</div></div>
                  <div style={{ position: 'relative', width: '240px' }}>
                    <div className="input-wrapper" style={{ height: '40px', width: '100%' }}>
                      <input
                        className="search-input"
                        style={{ fontSize: '0.8rem', padding: '0 15px', background: '#222' }}
                        placeholder="+ SEARCH & ADD PEER"
                        value={manualPeerTicker}
                        onChange={(e) => handlePeerSearchInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') { addManualPeer(); }
                          if (e.key === 'Escape') { setPeerSearchOpen(false); setPeerSearchResults([]); }
                        }}
                        onFocus={() => manualPeerTicker && setPeerSearchOpen(true)}
                        onBlur={() => setTimeout(() => setPeerSearchOpen(false), 180)}
                        autoComplete="off"
                      />
                    </div>
                    {peerSearchOpen && (peerSearchLoading || peerSearchResults.length > 0) && (
                      <div style={{
                        position: 'absolute', top: '44px', left: 0, right: 0, zIndex: 999,
                        background: '#0d1b2a', border: '1px solid #00d4ff33', borderRadius: '6px',
                        boxShadow: '0 8px 24px rgba(0,0,0,0.6)', overflow: 'hidden',
                      }}>
                        {peerSearchLoading && (
                          <div style={{ padding: '10px 14px', color: '#556', fontSize: '0.75rem' }}>Searching…</div>
                        )}
                        {peerSearchResults.map((r) => (
                          <div
                            key={r.symbol}
                            onMouseDown={() => addManualPeer(r.symbol)}
                            style={{
                              padding: '9px 14px', cursor: 'pointer', display: 'flex',
                              justifyContent: 'space-between', alignItems: 'center',
                              borderBottom: '1px solid #ffffff08',
                              transition: 'background 0.12s',
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = '#162840'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                          >
                            <span style={{ color: '#00d4ff', fontWeight: 'bold', fontSize: '0.82rem', fontFamily: 'JetBrains Mono, monospace' }}>{r.symbol}</span>
                            <span style={{ color: '#99aabb', fontSize: '0.72rem', maxWidth: '130px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                          </div>
                        ))}
                        {!peerSearchLoading && peerSearchResults.length === 0 && manualPeerTicker.length > 1 && (
                          <div style={{ padding: '10px 14px', color: '#556', fontSize: '0.75rem' }}>No matches — press Enter to try direct symbol</div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                {data.peer_methodology?.tier_used && (
                  <div className="peers-methodology" title={(data.peer_methodology.filters || []).join('  →  ')}>
                    <span>⚙</span>
                    <span>
                      <b>Cohort methodology:</b> {data.peer_methodology.tier_used}
                      {' · '}{data.peer_methodology.candidates_screened} candidates screened
                      {' · '}cascade: {(data.peer_methodology.filters || []).join(' → ')}
                    </span>
                  </div>
                )}
                <div className="comps-scroll">
                  <table className="comps-table">
                    <thead>
                      <tr>
                        <th style={{ width: '30px' }} />
                        <th>SYMBOL</th>
                        <th>SIMILARITY</th>
                        <th>PRICE</th>
                        <th>MARKET CAP</th>
                        <th style={{ color: dealType === 'M&A' ? '#00d4ff' : '#666' }}>P/E</th>
                        <th style={{ color: dealType === 'LBO' || dealType === 'M&A' ? '#00d4ff' : '#666' }}>EV/EBITDA</th>
                        <th style={{ color: dealType === 'IPO' ? '#00d4ff' : '#666' }}>EV/SALES</th>
                        {dealType === 'LBO' && <th style={{ color: '#00d4ff' }}>NET DEBT/EBITDA</th>}
                        <th>ROIC</th>
                        <th>GROWTH</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="comps-row target-row">
                        <td /><td>{data.symbol}</td><td style={{ color: '#666' }}>TARGET</td>
                        <td>{data.price}</td><td>{formatMarketCap(data.market_cap)}</td>
                        <td>{data.target_ratios?.pe || '-'}</td>
                        <td>{data.target_ratios?.ev_ebitda || '-'}</td>
                        <td>{data.target_ratios?.ev_sales || '-'}</td>
                        {dealType === 'LBO' && <td>{data.target_ratios?.net_debt_ebitda || '-'}</td>}
                        <td>{data.target_ratios?.roic}</td>
                        <td>{data.target_ratios?.growth}%</td>
                      </tr>
                      {(() => {
                        const allPeerRows = [...(data.peers || []), ...customPeersData];
                        const catA = allPeerRows.filter(p => (p.category || 'A') === 'A');
                        const catB = allPeerRows.filter(p => p.category === 'B');
                        const renderPeerRow = (peer, i) => {
                          const isChecked = activePeers.includes(peer.symbol);
                          return (
                            <tr key={peer.symbol + i} className="comps-row" onClick={() => { setSelectedPeer(peer); setPeerSummaryExpanded(false); setShowPeerRaw(false); }} style={{ opacity: isChecked ? 1 : 0.5 }}>
                              <td onClick={(e) => { e.stopPropagation(); togglePeer(peer.symbol); }}><div className={`check-box ${isChecked ? 'checked' : ''}`} /></td>
                              <td>{peer.symbol}</td>
                              <td>
                                <div style={{ fontSize: '0.7rem', marginBottom: '2px', color: getColor(peer.similarity, 'similarity') }}>{peer.similarity}%</div>
                                <div className="sim-bar"><div className="sim-fill" style={{ width: `${peer.similarity}%`, background: getColor(peer.similarity, 'similarity') }} /></div>
                              </td>
                              <td>{peer.price}</td>
                              <td>{formatMarketCap(peer.mkt_cap)}</td>
                              <td style={{ color: peer.pe !== '-' && peer.pe < 25 ? '#00ff00' : '#ccc' }}>{peer.pe}</td>
                              <td>{peer.ev_ebitda}</td>
                              <td style={{ color: peer.net_debt_ebitda > 3 ? '#ff4444' : '#ccc' }}>{peer.net_debt_ebitda}</td>
                              {dealType === 'LBO' && <td>{peer.net_debt_ebitda}</td>}
                              <td>{peer.roic}</td>
                              <td>{peer.growth_raw ? (peer.growth_raw * 100).toFixed(1) : '-'}%</td>
                            </tr>
                          );
                        };
                        return (
                          <>
                            {catA.length > 0 && (
                              <tr className="comps-cat-head"><td colSpan="11" style={{ background: '#001a2e', color: '#00d4ff', fontSize: '0.68rem', fontWeight: 'bold', letterSpacing: '0.08em' }}>▸ CATEGORY A — DIRECT SECTOR & INDUSTRY COMPARABLES</td></tr>
                            )}
                            {catA.map((p, i) => renderPeerRow(p, i))}
                            {catB.length > 0 && (
                              <tr className="comps-cat-head"><td colSpan="11" style={{ background: '#1a1400', color: '#ffa500', fontSize: '0.68rem', fontWeight: 'bold', letterSpacing: '0.08em' }}>▸ CATEGORY B — SCALE BENCHMARKS (SAME SECTOR)</td></tr>
                            )}
                            {catB.map((p, i) => renderPeerRow(p, i + catA.length))}
                          </>
                        );
                      })()}
                    </tbody>
                  </table>
                </div>
                <div className="calc-footer">
                  <div className="metric"><div style={{ color: '#666', fontSize: '0.75rem' }}>PEERS SELECTED</div><div style={{ fontSize: '1.5rem', fontWeight: 'bold' }}>{impliedValuation.count}</div></div>
                  <div className="metric"><div style={{ color: '#666', fontSize: '0.75rem' }}>AVG P/E (HARMONIC)</div><div style={{ fontSize: '1.2rem', fontFamily: 'JetBrains Mono' }}>{(impliedValuation.avgPe || 0).toFixed(1)}x</div></div>
                  <div className="metric"><div style={{ color: '#666', fontSize: '0.75rem' }}>AVG EV/EBITDA</div><div style={{ fontSize: '1.2rem', fontFamily: 'JetBrains Mono' }}>{(impliedValuation.avgEv || 0).toFixed(1)}x</div></div>
                  <div className="implied">
                    <div style={{ color: '#00d4ff', fontSize: '0.7rem', fontWeight: 'bold' }}>IMPLIED SHARE PRICE</div>
                    <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: '#fff' }}>{data.currency_symbol}{impliedValuation.finalPrice}</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── PEER POPUP ───────────────────────────────────────────────────────── */}
      {selectedPeer && (
        <div className="peer-popup" role="dialog" aria-modal="true">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <div>
              <h3 style={{ margin: 0, color: '#00d4ff', fontSize: '1.1rem' }}>{selectedPeer.symbol}</h3>
              <div style={{ color: '#fff', fontSize: '0.85rem', marginTop: '4px' }}>{selectedPeer.name}</div>
              {shortInfoLine && <div style={{ color: '#888', fontSize: '0.75rem', marginTop: '6px' }}>{shortInfoLine}</div>}
            </div>
            <button onClick={() => setSelectedPeer(null)} style={{ background: 'none', border: 'none', color: '#fff', fontSize: '1rem', cursor: 'pointer' }}>✕</button>
          </div>
          <div style={{ display: 'flex', gap: '10px', marginBottom: '10px', fontSize: '0.85rem', color: '#ccc' }}>
            <div style={{ flex: 1 }}><div style={{ color: '#666', fontSize: '0.65rem' }}>PRICE</div><div>{selectedPeer.price || '-'}</div></div>
            <div style={{ flex: 1 }}><div style={{ color: '#666', fontSize: '0.65rem' }}>MKT CAP</div><div>{selectedPeer.mkt_cap || '-'}</div></div>
          </div>
          <div style={{ display: 'flex', gap: '10px', marginBottom: '12px', fontSize: '0.85rem', color: '#ccc' }}>
            <div style={{ flex: 1 }}><div style={{ color: '#666', fontSize: '0.65rem' }}>P/E</div><div>{selectedPeer.pe || '-'}</div></div>
            <div style={{ flex: 1 }}><div style={{ color: '#666', fontSize: '0.65rem' }}>EV/EBITDA</div><div>{selectedPeer.ev_ebitda || '-'}</div></div>
          </div>
          <div style={{ fontSize: '0.85rem', color: '#aaa', lineHeight: 1.4, maxHeight: peerSummaryExpanded ? '12rem' : '4rem', overflow: peerSummaryExpanded ? 'auto' : 'hidden' }}>
            {currentPeerSummary ? (peerSummaryExpanded ? currentPeerSummary : shortSummaryFirst) : 'No summary available.'}
          </div>
          {currentPeerSummary && currentPeerSummary.length > shortSummaryFirst.length && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px' }}>
              <button onClick={() => setPeerSummaryExpanded(!peerSummaryExpanded)} style={{ background: 'none', border: 'none', color: '#00d4ff', cursor: 'pointer', fontSize: '0.85rem', padding: 0 }}>{peerSummaryExpanded ? 'Show less' : 'Read more'}</button>
              <button onClick={() => setShowPeerRaw(!showPeerRaw)} style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', fontSize: '0.75rem', padding: 0 }}>{showPeerRaw ? 'Hide details' : 'Show details'}</button>
            </div>
          )}
          {showPeerRaw && <pre style={{ marginTop: '10px', background: '#0b0b0b', padding: '10px', borderRadius: '8px', maxHeight: '160px', overflow: 'auto', color: '#ccc', fontSize: '0.75rem' }}>{JSON.stringify(selectedPeer, null, 2)}</pre>}
        </div>
      )}

      {/* ── PROFILE MODAL ────────────────────────────────────────────────────── */}
      {showProfile && (
        <ProfileModal
          user={user}
          watchlist={watchlist}
          onClose={() => setShowProfile(false)}
          onSelect={(sym) => fetchAnalysis(sym)}
          onRemove={(sym) => user && !user.isGuest && removeFromWatchlist(user.uid, sym)}
          onSignOut={() => { signOut(auth).then(() => { setUser(null); setData(null); setShowProfile(false); }); }}
        />
      )}

      <div className="footer-bar" style={{ color: '#888', fontSize: '0.75rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        ⚠️ EDUCATIONAL USE ONLY — This tool is a valuation simulation and does not constitute financial advice.
      </div>
    </>
  );
}

// ── Financial Table Component ─────────────────────────────────────────────────
function FinTable({ rows, data, isMargin }) {
  const dates = data.map((y) => y.date);
  return (
    <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: '440px' }}>
      <table className="fin-table" style={{ minWidth: '600px' }}>
        <thead>
          <tr>
            <th style={{ width: '180px', textAlign: 'left' }}>METRIC</th>
            {dates.map((d, i) => <th key={i}>{d}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => {
            const hasData = data.some((y) => y[k] && y[k] !== '-');
            return (
              <tr key={k} className="fin-row" style={{ opacity: hasData ? 1 : 0.35 }}>
                <td style={{ color: isMargin && isMargin(k) ? '#00d4ff' : '#aaa', fontSize: '0.78rem' }}>{k}</td>
                {data.map((y, i) => {
                  const val = y[k] || '-';
                  return (
                    <td key={i} style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.82rem', color: isMargin && isMargin(k) ? '#88ccff' : '#fff' }}>
                      {val}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default App;
