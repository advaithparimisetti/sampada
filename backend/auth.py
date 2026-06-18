# backend/auth.py
"""
Firebase Admin SDK integration.
Credentials are loaded from (in priority order):
  1. FIREBASE_SERVICE_ACCOUNT_JSON env var — full JSON string (production / Render)
  2. FIREBASE_SERVICE_ACCOUNT env var — path to a local .json file (local dev)
Auth is opt-in per-route; all analysis endpoints remain available without auth.
"""
import os
import json
from pathlib import Path

# Load backend/.env for local dev
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except Exception:
    pass

_app = None
_diagnostic: dict = {"status": "pending", "uid_test": None, "firestore": False, "error": None}


def _load_cred_obj():
    """Return a firebase_admin.credentials.Certificate or None."""
    from firebase_admin import credentials

    # Option 1: full JSON string in env (production)
    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if json_str:
        try:
            return credentials.Certificate(json.loads(json_str))
        except Exception as e:
            print(f"[Firebase] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")

    # Option 2: path to local file (local dev)
    cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if cred_path and Path(cred_path).is_file():
        return credentials.Certificate(cred_path)

    return None


def run_startup_diagnostic() -> dict:
    """
    Called once at application startup.
    Attempts to initialize Firebase Admin, verify connectivity, and
    write+read a test document in Firestore.
    Returns a diagnostic dict that is logged and exposed via /api/diagnostics.
    """
    global _app, _diagnostic

    cred = _load_cred_obj()
    if cred is None:
        _diagnostic = {
            "status": "disabled",
            "uid_test": None,
            "firestore": False,
            "error": "No Firebase credentials found — running in guest mode",
        }
        print(f"[Firebase] {_diagnostic['error']}")
        return _diagnostic

    try:
        import firebase_admin
        from firebase_admin import auth, firestore

        if not firebase_admin._apps:
            _app = firebase_admin.initialize_app(cred)
        else:
            _app = firebase_admin.get_app()

        # Verify auth service responds
        try:
            # List users is a lightweight health check (returns max 1 result)
            page = auth.list_users(max_results=1, app=_app)
            _diagnostic["uid_test"] = "auth_ok"
        except Exception as ae:
            _diagnostic["uid_test"] = f"auth_check_failed: {ae}"

        # Verify Firestore write
        try:
            db = firestore.client(app=_app)
            ref = db.collection("_diagnostics").document("startup")
            ref.set({"ts": firestore.SERVER_TIMESTAMP, "status": "ok"})
            _diagnostic["firestore"] = True
        except Exception as fe:
            _diagnostic["firestore"] = False
            _diagnostic["error"] = f"Firestore write failed: {fe}"

        _diagnostic["status"] = "ok" if _diagnostic["firestore"] else "partial"
        print(f"[Firebase] Startup diagnostic: {_diagnostic['status']} | "
              f"Auth: {_diagnostic['uid_test']} | Firestore: {_diagnostic['firestore']}")

    except Exception as exc:
        _diagnostic = {
            "status": "error",
            "uid_test": None,
            "firestore": False,
            "error": str(exc),
        }
        print(f"[Firebase] Init error: {exc}")

    return _diagnostic


def _get_app():
    global _app
    if _app is not None:
        return _app
    # Lazy-init if startup diagnostic wasn't called
    run_startup_diagnostic()
    return _app


def verify_token(id_token: str) -> dict:
    """Verify a Firebase ID token. Returns decoded claims or raises ValueError."""
    app = _get_app()
    if app is None:
        raise ValueError("Firebase not configured on this server")
    from firebase_admin import auth
    try:
        return auth.verify_id_token(id_token, app=app)
    except Exception as e:
        raise ValueError(f"Invalid token: {e}")


def save_session(uid: str, ticker: str, result: dict):
    """Persist an analysis session to Firestore. Never raises — fire-and-forget."""
    try:
        app = _get_app()
        if app is None:
            return
        from firebase_admin import firestore
        db = firestore.client(app=app)
        db.collection("users").document(uid).collection("sessions").add({
            "ticker": ticker,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "verdict": result.get("verdict"),
            "implied_price": (result.get("valuation_analysis") or {}).get("implied_price"),
            "upside": (result.get("valuation_analysis") or {}).get("upside"),
            "sector": result.get("sector"),
            "name": result.get("name"),
        })
    except Exception as exc:
        print(f"[Firebase] Firestore save error: {exc}")
