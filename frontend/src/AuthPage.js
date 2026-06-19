// src/AuthPage.js
import React, { useState } from 'react';
import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
} from 'firebase/auth';
import { doc, setDoc, serverTimestamp } from 'firebase/firestore';
import { auth, db } from './firebase';

export default function AuthPage({ onAuth }) {
  const [mode, setMode]       = useState('login');   // 'login' | 'signup'
  const [email, setEmail]     = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError]     = useState('');
  const [loading, setLoading] = useState(false);

  const isFirebaseConfigured = auth?.app?.options?.apiKey !== 'YOUR_API_KEY';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!isFirebaseConfigured) {
      // Guest mode: bypass Firebase when not configured
      onAuth({ uid: 'guest', email: email || 'guest@sampada.ai', isGuest: true });
      return;
    }

    if (mode === 'signup' && password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }

    setLoading(true);
    try {
      let credential;
      if (mode === 'signup') {
        credential = await createUserWithEmailAndPassword(auth, email, password);
        // Create user profile in Firestore
        await setDoc(doc(db, 'users', credential.user.uid), {
          email,
          createdAt: serverTimestamp(),
        });
      } else {
        credential = await signInWithEmailAndPassword(auth, email, password);
      }
      onAuth(credential.user);
    } catch (err) {
      const msgs = {
        'auth/email-already-in-use': 'Email is already registered.',
        'auth/user-not-found': 'No account found with this email.',
        'auth/wrong-password': 'Incorrect password.',
        'auth/invalid-email': 'Invalid email address.',
        'auth/too-many-requests': 'Too many attempts. Try again later.',
      };
      setError(msgs[err.code] || err.message);
    }
    setLoading(false);
  };

  return (
    <div style={styles.page}>
      <div style={styles.blob} />

      <div style={styles.card}>
        {/* Brand */}
        <h1 style={styles.brand}>
          SAMPA<span style={{ color: '#00d4ff' }}>DA</span>
        </h1>
        <p style={styles.tagline}>Institutional-Grade Equity Intelligence</p>

        {/* Mode toggle */}
        <div style={styles.toggleRow}>
          <button
            style={{ ...styles.toggleBtn, ...(mode === 'login' ? styles.toggleActive : {}) }}
            onClick={() => { setMode('login'); setError(''); }}
          >
            SIGN IN
          </button>
          <button
            style={{ ...styles.toggleBtn, ...(mode === 'signup' ? styles.toggleActive : {}) }}
            onClick={() => { setMode('signup'); setError(''); }}
          >
            CREATE ACCOUNT
          </button>
        </div>

        {!isFirebaseConfigured && (
          <div style={styles.notice}>
            ⚡ Firebase not configured — running in guest mode. Sessions will not be saved.
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ width: '100%' }}>
          <input
            style={styles.input}
            type="email"
            placeholder="EMAIL ADDRESS"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
          <input
            style={styles.input}
            type="password"
            placeholder="PASSWORD"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
          />
          {mode === 'signup' && (
            <input
              style={styles.input}
              type="password"
              placeholder="CONFIRM PASSWORD"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              autoComplete="new-password"
            />
          )}

          {error && <div style={styles.error}>{error}</div>}

          <button
            type="submit"
            disabled={loading}
            style={{ ...styles.submitBtn, ...(loading ? styles.submitDisabled : {}) }}
          >
            {loading ? 'AUTHENTICATING...' : mode === 'login' ? 'SIGN IN →' : 'CREATE ACCOUNT →'}
          </button>
        </form>

        <div style={styles.divider}>
          <span style={styles.dividerLine} />
          <span style={styles.dividerText}>OR</span>
          <span style={styles.dividerLine} />
        </div>

        <button
          style={{ ...styles.submitBtn, marginTop: 0, background: 'transparent', border: '1px solid #1a2a3a', color: '#8aa', boxShadow: 'none' }}
          onClick={() => onAuth({ uid: 'guest', email: 'guest@sampada.ai', isGuest: true })}
        >
          CONTINUE AS GUEST →
        </button>
        <p style={styles.guestNote}>
          Full analysis, valuation & exports. Watchlist requires an account.
        </p>

        <p style={styles.disclaimer}>
          For educational purposes only. Not financial advice.
        </p>
      </div>
    </div>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    background: '#050505',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: "'Inter', sans-serif",
    position: 'relative',
    overflow: 'hidden',
  },
  blob: {
    position: 'absolute',
    width: '600px',
    height: '600px',
    borderRadius: '50%',
    background: 'radial-gradient(circle, rgba(0,212,255,0.06) 0%, transparent 70%)',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    pointerEvents: 'none',
  },
  card: {
    width: '420px',
    background: 'rgba(15,20,30,0.92)',
    border: '1px solid #1a2a3a',
    borderRadius: '20px',
    padding: '48px 44px',
    backdropFilter: 'blur(20px)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    position: 'relative',
    zIndex: 1,
  },
  brand: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: '2rem',
    fontWeight: 900,
    color: '#fff',
    margin: '0 0 6px',
    letterSpacing: '-1px',
  },
  tagline: {
    fontSize: '0.72rem',
    color: '#446',
    letterSpacing: '2px',
    textTransform: 'uppercase',
    margin: '0 0 36px',
  },
  toggleRow: {
    display: 'flex',
    gap: '0',
    background: '#0a0f18',
    borderRadius: '10px',
    padding: '4px',
    marginBottom: '28px',
    width: '100%',
  },
  toggleBtn: {
    flex: 1,
    padding: '9px 0',
    border: 'none',
    borderRadius: '7px',
    background: 'transparent',
    color: '#446688',
    fontSize: '0.72rem',
    fontWeight: 700,
    letterSpacing: '1.5px',
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  toggleActive: {
    background: '#0d1b2a',
    color: '#00d4ff',
    boxShadow: '0 0 12px rgba(0,212,255,0.15)',
  },
  notice: {
    background: 'rgba(0,212,255,0.07)',
    border: '1px solid rgba(0,212,255,0.2)',
    borderRadius: '8px',
    padding: '10px 14px',
    fontSize: '0.74rem',
    color: '#00a8cc',
    marginBottom: '20px',
    width: '100%',
    boxSizing: 'border-box',
  },
  input: {
    width: '100%',
    boxSizing: 'border-box',
    background: '#0a0f18',
    border: '1px solid #1a2a3a',
    borderRadius: '10px',
    color: '#fff',
    fontSize: '0.82rem',
    letterSpacing: '1px',
    padding: '14px 18px',
    marginBottom: '14px',
    outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
  },
  error: {
    background: 'rgba(255,68,68,0.1)',
    border: '1px solid rgba(255,68,68,0.3)',
    borderRadius: '8px',
    padding: '10px 14px',
    fontSize: '0.78rem',
    color: '#ff6666',
    marginBottom: '14px',
    width: '100%',
    boxSizing: 'border-box',
  },
  submitBtn: {
    width: '100%',
    padding: '15px',
    background: 'linear-gradient(135deg, #0077aa, #00d4ff)',
    border: 'none',
    borderRadius: '10px',
    color: '#fff',
    fontSize: '0.82rem',
    fontWeight: 700,
    letterSpacing: '2px',
    cursor: 'pointer',
    transition: 'opacity 0.2s',
    marginTop: '6px',
    boxShadow: '0 4px 24px rgba(0,212,255,0.18)',
  },
  submitDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
  divider: {
    display: 'flex',
    alignItems: 'center',
    width: '100%',
    margin: '20px 0 16px',
    gap: '12px',
  },
  dividerLine: {
    flex: 1,
    height: '1px',
    background: '#1a2a3a',
  },
  dividerText: {
    fontSize: '0.6rem',
    color: '#445',
    letterSpacing: '2px',
  },
  guestNote: {
    marginTop: '10px',
    fontSize: '0.65rem',
    color: '#556',
    textAlign: 'center',
    letterSpacing: '0.3px',
  },
  disclaimer: {
    marginTop: '24px',
    fontSize: '0.65rem',
    color: '#334',
    textAlign: 'center',
    letterSpacing: '0.5px',
  },
};
