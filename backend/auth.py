# backend/auth.py
"""
Firebase Admin SDK integration.
Credentials are loaded from (in priority order):
  1. FIREBASE_SERVICE_ACCOUNT_JSON env var — raw JSON OR base64-encoded JSON
     (production / Render). Base64 is recommended: it sidesteps every newline /
     quote-escaping quirk that hosting providers' env injectors introduce.
  2. FIREBASE_SERVICE_ACCOUNT env var — path to a local .json file (local dev)
Auth is opt-in per-route; all analysis endpoints remain available without auth.
"""
import os
import json
import base64
import binascii
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


def _parse_service_account(raw: str):
    """
    Parse a Firebase service-account JSON from an environment variable, tolerating
    the escaping quirks introduced by hosting providers' env-var injectors (Render,
    Heroku, etc.). Accepts, in order:
      1. Plain JSON.
      2. Base64-encoded JSON (recommended for Render — immune to all escaping issues).
      3. JSON whose `private_key` newlines were mangled (literal newlines or
         double-escaped `\\n`).
    Returns a dict or None.
    """
    if not raw:
        return None
    raw = raw.strip().strip('"').strip("'").strip()

    # Attempt 1: straight JSON
    try:
        return _fix_private_key(json.loads(raw))
    except Exception:
        pass

    # Attempt 2: base64-encoded JSON
    try:
        decoded = base64.b64decode(raw, validate=False).decode("utf-8").strip()
        if decoded.startswith("{"):
            return _fix_private_key(json.loads(decoded))
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        pass

    # Attempt 3: real newlines leaked into the JSON body (only legal inside the
    # private_key string). Escape bare newlines so the document parses, then repair.
    try:
        repaired = raw.replace("\r\n", "\n").replace("\n", "\\n")
        return _fix_private_key(json.loads(repaired))
    except Exception as e:
        # Safe diagnostic (no secret leaked): show shape so the user can debug.
        first = raw[:1]
        looks = ("json-object" if first == "{" else
                 "json-array" if first == "[" else
                 "path-like" if first in "/\\" or raw[:2].lower() == "c:" else
                 "other")
        print(f"[Firebase] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e} "
              f"| len={len(raw)} first_char={first!r} shape={looks} "
              f"| TIP: base64-encode the JSON file and paste that instead "
              f"(`base64 -w0 service-account.json`).")
        return None


def _fix_private_key(data: dict) -> dict:
    """Ensure the PEM private_key has real newlines (not literal backslash-n)."""
    if isinstance(data, dict) and isinstance(data.get("private_key"), str):
        pk = data["private_key"]
        if "\\n" in pk and "\n" not in pk:
            data["private_key"] = pk.replace("\\n", "\n")
    return data


def _load_cred_obj():
    """Return a firebase_admin.credentials.Certificate or None."""
    from firebase_admin import credentials

    # Option 1: JSON (or base64 JSON) string in env (production)
    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    parsed = _parse_service_account(json_str)
    if parsed:
        try:
            return credentials.Certificate(parsed)
        except Exception as e:
            print(f"[Firebase] Parsed JSON but Certificate() rejected it: {e}")

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
