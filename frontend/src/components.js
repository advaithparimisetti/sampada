/* src/components.js */
import React from 'react';

// ─── Heart (favorite) toggle ──────────────────────────────────────────────────
export const HeartButton = ({ active, busy, onClick }) => (
    <button
        className={`heart-btn ${active ? 'active' : ''}`}
        onClick={(e) => { e.stopPropagation(); onClick && onClick(); }}
        disabled={busy}
        title={active ? 'Remove from watchlist' : 'Add to watchlist'}
        aria-pressed={active}
    >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path
                d="M12 21s-7.5-4.7-10-9.3C.4 8.5 1.8 5 5.2 5c2 0 3.3 1.1 4 2.2.7-1.1 2-2.2 4-2.2 3.4 0 4.8 3.5 3.2 6.7C19.5 16.3 12 21 12 21z"
                fill={active ? '#ff3b6b' : 'none'}
                stroke={active ? '#ff3b6b' : '#8899aa'}
                strokeWidth="1.8"
                strokeLinejoin="round"
            />
        </svg>
    </button>
);

// ─── Profile & Watchlist modal ────────────────────────────────────────────────
export const ProfileModal = ({ user, watchlist, onClose, onSelect, onRemove, onSignOut }) => {
    const isGuest = !!user?.isGuest;
    const email = isGuest ? 'Guest session' : (user?.email || 'Guest user');
    const initial = isGuest ? 'G' : (email || 'U').charAt(0).toUpperCase();
    const verdictColor = (v) =>
        v?.includes('POSITIVE') ? '#00e576' : v?.includes('NEGATIVE') ? '#ff4444' : '#ffa500';

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content profile-modal" onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
                    <h2 style={{ margin: 0, color: '#00d4ff', fontSize: '1.2rem', letterSpacing: '1px' }}>PROFILE</h2>
                    <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#666', fontSize: '1.5rem', cursor: 'pointer' }}>✕</button>
                </div>

                {/* Identity */}
                <div className="profile-identity">
                    <div className="profile-avatar-lg">{initial}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ color: '#fff', fontSize: '1rem', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis' }}>{email}</div>
                        <div style={{ color: '#556', fontSize: '0.72rem', marginTop: '2px' }}>
                            {isGuest
                                ? 'Browsing without an account'
                                : `${watchlist?.length || 0} stock${(watchlist?.length || 0) === 1 ? '' : 's'} on watchlist`}
                        </div>
                    </div>
                    <button className="signout-btn" onClick={onSignOut}>{isGuest ? 'SIGN IN' : 'SIGN OUT'}</button>
                </div>

                {/* Watchlist */}
                <div style={{ marginTop: '28px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#667', fontWeight: 700, letterSpacing: '1.5px', marginBottom: '14px' }}>
                        ★ WATCHLIST
                    </div>

                    {isGuest ? (
                        <div style={{ textAlign: 'center', padding: '26px 16px', background: '#0d1622', border: '1px solid #18293a', borderRadius: '12px' }}>
                            <div style={{ color: '#8aa', fontSize: '0.85rem', marginBottom: '14px' }}>
                                The watchlist saves your stocks across sessions. Create a free account to unlock it.
                            </div>
                            <button className="signout-btn" style={{ borderColor: '#0a5', color: '#0d9' }} onClick={onSignOut}>
                                SIGN IN / CREATE ACCOUNT
                            </button>
                        </div>
                    ) : (!watchlist || watchlist.length === 0) ? (
                        <div style={{ color: '#445', fontStyle: 'italic', fontSize: '0.85rem', padding: '30px 0', textAlign: 'center' }}>
                            No saved stocks yet. Tap the ♥ next to a ticker to add it here.
                        </div>
                    ) : (
                        <div className="watchlist-grid">
                            {watchlist.map((w) => (
                                <div key={w.ticker} className="watch-card" onClick={() => onSelect && onSelect(w.ticker)}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                        <div style={{ minWidth: 0 }}>
                                            <div className="watch-sym">{w.ticker}</div>
                                            <div className="watch-name">{w.name}</div>
                                        </div>
                                        <button
                                            className="watch-remove"
                                            title="Remove"
                                            onClick={(e) => { e.stopPropagation(); onRemove && onRemove(w.ticker); }}
                                        >✕</button>
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px' }}>
                                        <span style={{ fontFamily: 'JetBrains Mono, monospace', color: '#cdd', fontSize: '0.85rem' }}>
                                            {w.price != null ? `${w.currency_symbol || ''}${w.price}` : '—'}
                                        </span>
                                        {w.verdict && (
                                            <span style={{ fontSize: '0.62rem', fontWeight: 700, color: verdictColor(w.verdict), letterSpacing: '0.5px' }}>
                                                {w.verdict}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export const METHODOLOGY = {
    dcf: { title: "Intrinsic Value (DCF)", text: "We run 3 parallel Monte Carlo simulations (Bear, Base, Bull) with 2,000 iterations total. Key inputs like WACC and Growth Rate are correlated to simulate business cycle volatility." },
    comps: { title: "Relative Value (Comps)", text: "Similarity-Weighted Harmonic Mean. Peers are weighted by their statistical similarity (Size, Margin, Growth) to the target. We filter outliers using IQR logic." },
    blended: { title: "Blended Fair Value", text: "Weighted average: 60% Intrinsic (DCF) + 40% Relative (Comps). Anchors theoretical value to market reality." },
    wacc: { title: "WACC (Dynamic Cost of Capital)", text: "Risk-Free Rate (Local Bond Yield) + Beta Adjusted Equity Premium + Synthetic Cost of Debt. Includes sector-specific floors to prevent artificially low discount rates." },
    growth: { title: "Growth (ROIC Derived)", text: "Derived where possible from Reinvestment Rate * ROIC. Fades linearly to terminal GDP rate over 5 years. This prevents overestimating growth for mature firms." },
    range: { title: "Volatility Spread", text: "The spread between the 25th percentile (Bear) and 75th percentile (Bull) outcomes. A wide spread triggers a lower Confidence Score." }
};

export const InfoBtn = ({ id, setActiveInfo }) => (
    <span 
      onClick={(e) => { e.stopPropagation(); setActiveInfo(METHODOLOGY[id]); }}
      style={{ display:'inline-flex', alignItems:'center', justifyContent:'center', width:'14px', height:'14px', borderRadius:'50%', border:'1px solid #666', color:'#888', fontSize:'9px', marginLeft:'6px', cursor:'pointer', verticalAlign:'middle', transition: '0.2s' }}
      onMouseEnter={(e) => { e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = '#fff'; }}
      onMouseLeave={(e) => { e.currentTarget.style.color = '#888'; e.currentTarget.style.borderColor = '#666'; }}
    >?</span>
);

export const FootballField = ({ ranges, currentPrice }) => {
    let min = currentPrice;
    let max = currentPrice;
    Object.values(ranges).forEach(r => {
        if(r[0] < min) min = r[0];
        if(r[1] > max) max = r[1];
    });
    min = min * 0.9;
    max = max * 1.1;
    const width = max - min;

    const BarRow = ({ label, low, high }) => {
        const leftPct = ((low - min) / width) * 100;
        const widthPct = ((high - low) / width) * 100;
        return (
            <div style={{display:'flex', alignItems:'center', marginBottom:'8px'}}>
                <div style={{width:'100px', fontSize:'0.7rem', color:'#aaa', textAlign:'right', marginRight:'10px'}}>{label}</div>
                <div style={{flex:1, position:'relative', height:'20px', background:'#222', borderRadius:'4px'}}>
                     <div style={{
                         position:'absolute', left:`${leftPct}%`, width:`${widthPct}%`, 
                         background: 'linear-gradient(90deg, #444, #666)', height:'100%', borderRadius:'4px',
                         display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 5px', fontSize:'0.6rem'
                     }}>
                        <span>{low.toFixed(1)}</span><span>{high.toFixed(1)}</span>
                     </div>
                </div>
            </div>
        );
    };

    const currentPos = ((currentPrice - min) / width) * 100;

    return (
        <div style={{position:'relative', padding:'10px 0'}}>
            <BarRow label="52 Week Range" low={ranges.fifty_two_week[0]} high={ranges.fifty_two_week[1]} />
            <BarRow label="Analyst Targets" low={ranges.analyst_target[0]} high={ranges.analyst_target[1]} />
            <BarRow label="Comps Range" low={ranges.comps_range[0]} high={ranges.comps_range[1]} />
            <BarRow label="DCF Range" low={ranges.dcf_range[0]} high={ranges.dcf_range[1]} />
            <div style={{
                position:'absolute', top:0, bottom:0, left:`calc(100px + 10px + ${currentPos}%)`, 
                width:'2px', background:'#00d4ff', border:'1px dashed #000', zIndex:10
            }}>
                <div style={{position:'absolute', top:'-15px', left:'-50%', transform:'translateX(-25%)', color:'#00d4ff', fontSize:'0.7rem', fontWeight:'bold'}}>Now</div>
            </div>
        </div>
    );
};

// Maps yfinance recommendationMean (1=Strong Buy … 5=Strong Sell) to display data
const _MEAN_CONFIG = [
    { max: 1.5, label: 'STRONG BUY',  color: '#00e576', bg: '#003318' },
    { max: 2.5, label: 'BUY',          color: '#66dd88', bg: '#002a14' },
    { max: 3.5, label: 'HOLD',         color: '#ffa500', bg: '#2a1a00' },
    { max: 4.5, label: 'UNDERPERFORM', color: '#ff7744', bg: '#2a0e00' },
    { max: 5.1, label: 'SELL',         color: '#ff4444', bg: '#2a0000' },
];

const _meanConfig = (mean) => _MEAN_CONFIG.find(c => mean <= c.max) || _MEAN_CONFIG[4];

// Semi-circular gauge rendered in pure SVG — no library needed
const ConsensusGauge = ({ mean }) => {
    if (!mean) return null;
    const cfg  = _meanConfig(mean);
    // Arc goes from 180° (left = Strong Buy = 1) to 0° (right = Strong Sell = 5)
    // pct: 0 at Strong Buy, 1 at Strong Sell
    const pct  = (mean - 1) / 4;
    const angle = Math.PI - pct * Math.PI;          // radians, 0=right, π=left
    const cx = 70, cy = 58, r = 48;
    const nx  = cx + r * Math.cos(angle);
    const ny  = cy - r * Math.sin(angle);

    // Build arc segments (5 equal bands)
    const segColors = ['#00e576', '#66dd88', '#ffa500', '#ff7744', '#ff4444'];
    const arcs = segColors.map((c, i) => {
        const a1 = Math.PI - (i / 5) * Math.PI;
        const a2 = Math.PI - ((i + 1) / 5) * Math.PI;
        const x1 = cx + r * Math.cos(a1), y1 = cy - r * Math.sin(a1);
        const x2 = cx + r * Math.cos(a2), y2 = cy - r * Math.sin(a2);
        return (
            <path key={i}
                d={`M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`}
                fill="none" stroke={c} strokeWidth="10" strokeLinecap="butt"
            />
        );
    });

    return (
        <svg width="140" height="76" style={{overflow:'visible', display:'block', margin:'0 auto'}}>
            {arcs}
            {/* needle */}
            <line x1={cx} y1={cy} x2={nx} y2={ny}
                stroke="#fff" strokeWidth="2" strokeLinecap="round" />
            <circle cx={cx} cy={cy} r="4" fill="#fff" />
            {/* centre label */}
            <text x={cx} y={cy + 18} textAnchor="middle"
                fill={cfg.color} fontSize="10" fontWeight="bold" fontFamily="JetBrains Mono, monospace">
                {cfg.label}
            </text>
        </svg>
    );
};

export const ConsensusBar = ({ consensus }) => {
    const buy   = consensus?.buy  ?? 0;
    const hold  = consensus?.hold ?? 0;
    const sell  = consensus?.sell ?? 0;
    const total = (buy + hold + sell) || 0;
    const mean  = consensus?.recommendation_mean;   // 1–5 yfinance scale
    const rec   = consensus?.recommendation;
    const targetMean  = consensus?.target_mean;
    const targetLow   = consensus?.target_low;
    const targetHigh  = consensus?.target_high;
    const numAnalysts = consensus?.num_analysts;

    const hasGauge = mean && mean >= 1 && mean <= 5;
    const cfg = hasGauge ? _meanConfig(mean) : null;

    // Nothing at all to show
    if (!total && !hasGauge && !rec && !targetMean) return null;

    const buyW  = total ? (buy  / total) * 100 : 0;
    const holdW = total ? (hold / total) * 100 : 0;
    const sellW = total ? (sell / total) * 100 : 0;

    return (
        <div style={{marginTop:'10px'}}>

            {/* ── Gauge (shown when recommendationMean is available) ── */}
            {hasGauge && (
                <div style={{
                    background: cfg.bg, border: `1px solid ${cfg.color}33`,
                    borderRadius:'12px', padding:'14px 12px 8px', marginBottom:'10px',
                    display:'flex', alignItems:'center', gap:'16px',
                }}>
                    <ConsensusGauge mean={mean} />
                    <div style={{flex:1}}>
                        <div style={{fontSize:'0.6rem', color:'#445', letterSpacing:'0.08em', marginBottom:'4px'}}>
                            WALL STREET CONSENSUS
                        </div>
                        <div style={{fontSize:'1.3rem', fontWeight:'bold', color: cfg.color, fontFamily:'JetBrains Mono, monospace', lineHeight:1}}>
                            {cfg.label}
                        </div>
                        <div style={{fontSize:'0.65rem', color:'#556', marginTop:'4px'}}>
                            Score {mean.toFixed(2)} / 5.0
                            {numAnalysts ? ` · ${numAnalysts} analysts` : ''}
                        </div>
                        {rec && (
                            <div style={{fontSize:'0.65rem', color:'#778', marginTop:'2px'}}>
                                Key: {rec}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ── Stacked buy/hold/sell bar (when grade counts available) ── */}
            {total > 0 && (
                <>
                    <div style={{width:'100%', height:'10px', display:'flex', borderRadius:'5px', overflow:'hidden', marginBottom:'6px'}}>
                        <div style={{width:`${buyW}%`,  background:'#00e576', transition:'width 0.4s'}} title={`Buy: ${buy}`} />
                        <div style={{width:`${holdW}%`, background:'#ffa500', transition:'width 0.4s'}} title={`Hold: ${hold}`} />
                        <div style={{width:`${sellW}%`, background:'#ff4444', transition:'width 0.4s'}} title={`Sell: ${sell}`} />
                    </div>
                    <div style={{display:'flex', justifyContent:'space-between', fontSize:'0.63rem', color:'#667', marginBottom:'8px'}}>
                        <span style={{color:'#00e576'}}>▲ BUY {buy}</span>
                        <span style={{color:'#ffa500'}}>◆ HOLD {hold}</span>
                        <span style={{color:'#ff4444'}}>▼ SELL {sell}</span>
                    </div>
                </>
            )}

            {/* ── Price target row ── */}
            {targetMean && (
                <div style={{
                    background:'#0d1b2a', borderRadius:'8px', padding:'8px 12px',
                    display:'flex', justifyContent:'space-between', alignItems:'center',
                    borderLeft: `3px solid ${cfg ? cfg.color : '#00d4ff'}`,
                }}>
                    <div>
                        <div style={{fontSize:'0.6rem', color:'#445', letterSpacing:'0.06em'}}>MEAN PRICE TARGET</div>
                        <div style={{fontSize:'1rem', fontWeight:'bold', color:'#fff', fontFamily:'JetBrains Mono, monospace'}}>
                            ${targetMean.toFixed(2)}
                        </div>
                    </div>
                    {targetLow && targetHigh && (
                        <div style={{textAlign:'right'}}>
                            <div style={{fontSize:'0.6rem', color:'#445'}}>RANGE</div>
                            <div style={{fontSize:'0.72rem', color:'#778', fontFamily:'JetBrains Mono, monospace'}}>
                                ${targetLow.toFixed(2)} – ${targetHigh.toFixed(2)}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

const _fmtPrice = (val) => {
    if (val === undefined || val === null || val === 'N/A') return 'N/A';
    const n = parseFloat(val);
    if (isNaN(n)) return 'N/A';
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const _pct = (val) => {
    if (!val) return 'N/A';
    return (parseFloat(val) * 100).toFixed(2) + '%';
};

const MacroTile = ({ label, value, sub, accent }) => (
    <div style={{
        background: '#0d1b2a', borderRadius: '10px', padding: '14px 16px',
        borderLeft: `3px solid ${accent || '#00d4ff'}`, display: 'flex',
        flexDirection: 'column', gap: '4px',
    }}>
        <div style={{fontSize: '0.65rem', color: '#556', fontWeight: 'bold', letterSpacing: '0.07em', textTransform: 'uppercase'}}>{label}</div>
        <div style={{fontSize: '1.15rem', fontWeight: 'bold', color: '#fff', fontFamily: 'JetBrains Mono, monospace'}}>{value}</div>
        {sub && <div style={{fontSize: '0.65rem', color: '#445'}}>{sub}</div>}
    </div>
);

export const MarketDataCard = ({ data }) => {
    const wti  = data?.commodities?.WTI;
    const gold = data?.commodities?.GOLD;
    const brent = data?.commodities?.BRENT;
    const rf   = data?.macro?.risk_free_rate;
    const inf  = data?.macro?.inflation;

    return (
        <div style={{background: '#0a1520', border: '1px solid #1a2e40', borderRadius: '14px', padding: '18px 20px', marginBottom: '20px'}}>
            <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px'}}>
                <span style={{fontSize: '0.7rem', fontWeight: 'bold', color: '#00d4ff', letterSpacing: '0.1em'}}>MACRO  &  COMMODITIES</span>
                <span style={{fontSize: '0.6rem', color: '#334'}}>Live · yfinance futures</span>
            </div>
            <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px'}}>
                <MacroTile label="WTI Crude Oil" value={_fmtPrice(wti)} sub="$/bbl · West Texas" accent="#ffa500" />
                <MacroTile label="Gold Spot" value={_fmtPrice(gold)} sub="$/oz · XAUUSD" accent="#ffd700" />
                <MacroTile label="Brent Crude" value={_fmtPrice(brent)} sub="$/bbl · ICE" accent="#cc8844" />
                <MacroTile label="10Y Treasury Yield" value={rf ? _pct(rf) : 'N/A'} sub="US Risk-Free Rate" accent="#00d4ff" />
                <MacroTile label="CPI Inflation" value={inf ? _pct(inf) : 'N/A'} sub="YoY · Monthly CPI" accent="#aaaaff" />
                <MacroTile label="Natural Gas" value={_fmtPrice(data?.commodities?.NATURAL_GAS)} sub="$/MMBtu · Henry Hub" accent="#44ffaa" />
            </div>
        </div>
    );
};