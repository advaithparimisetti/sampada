// src/watchlist.js
// ─────────────────────────────────────────────────────────────────────────────
// Cloud Firestore watchlist helpers.
// Data model:  users/{uid}/watchlist/{TICKER}
//   { ticker, name, price, verdict, currency_symbol, addedAt }
// All reads/writes are scoped to the authenticated user's UID and are
// enforced server-side by firestore.rules.
// ─────────────────────────────────────────────────────────────────────────────
import {
  collection, doc, setDoc, deleteDoc, getDoc, onSnapshot, serverTimestamp,
} from 'firebase/firestore';
import { db } from './firebase';

// Firestore doc IDs can't contain '/'. Tickers are safe, but sanitise anyway.
const _docId = (ticker) => (ticker || '').toString().trim().toUpperCase().replace(/\//g, '-');

const _col = (uid) => collection(db, 'users', uid, 'watchlist');
const _ref = (uid, ticker) => doc(db, 'users', uid, 'watchlist', _docId(ticker));

/** Add a stock to the user's watchlist (idempotent). */
export async function addToWatchlist(uid, stock) {
  if (!uid || !stock?.ticker) return;
  await setDoc(_ref(uid, stock.ticker), {
    ticker: _docId(stock.ticker),
    name: stock.name || stock.ticker,
    price: stock.price ?? null,
    verdict: stock.verdict || null,
    currency_symbol: stock.currency_symbol || '$',
    addedAt: serverTimestamp(),
  });
}

/** Remove a stock from the user's watchlist. */
export async function removeFromWatchlist(uid, ticker) {
  if (!uid || !ticker) return;
  await deleteDoc(_ref(uid, ticker));
}

/** One-shot existence check. */
export async function isInWatchlist(uid, ticker) {
  if (!uid || !ticker) return false;
  const snap = await getDoc(_ref(uid, ticker));
  return snap.exists();
}

/**
 * Live subscription to the user's full watchlist.
 * Calls `cb(arrayOfStocks)` whenever it changes. Returns an unsubscribe fn.
 */
export function subscribeWatchlist(uid, cb) {
  if (!uid) { cb([]); return () => {}; }
  return onSnapshot(
    _col(uid),
    (snap) => {
      const items = [];
      snap.forEach((d) => items.push(d.data()));
      items.sort((a, b) => (b.addedAt?.seconds || 0) - (a.addedAt?.seconds || 0));
      cb(items);
    },
    () => cb([]) // on error (e.g. rules), fail soft to empty list
  );
}
