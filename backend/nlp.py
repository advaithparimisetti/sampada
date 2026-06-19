# backend/nlp.py
"""
Financial-domain sentiment engine.

Primary:  ProsusAI/finbert via the Hugging Face Inference API (free serverless).
          FinBERT is trained on financial text, so it reads jargon and context
          ("margins compressed due to temporary inventory actions") far better
          than a lexicon model.

Fallback: VADER (always available, zero-latency) whenever the HF token is unset,
          the model is cold-loading, the request times out, or anything errors.

We do NOT load transformers/torch locally — that would blow Render's 512 MB free
tier. All FinBERT inference is remote and strictly latency-bounded.
"""
import os
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_HF_TOKEN = os.environ.get("HF_API_TOKEN", "").strip()
_HF_URL = "https://api-inference.huggingface.co/models/ProsusAI/finbert"
_HF_TIMEOUT = float(os.environ.get("HF_TIMEOUT", "8"))
_MAX_BATCH = 25  # keep payloads small so the call stays fast

_vader = SentimentIntensityAnalyzer()
_cache: dict[str, float] = {}

# Finance keywords VADER's general lexicon misses — used only in the fallback path
# to keep no-token behaviour on par with the previous engine. When FinBERT is
# active these are ignored (we trust the contextual model).
_POS_KW = ("beat", "beats", "record", "surge", "jump", "soar", "strong", "upgrade",
           "raises guidance", "growth", "tops", "outperform")
_NEG_KW = ("miss", "misses", "fail", "drop", "plunge", "weak", "downgrade",
           "loss", "lawsuit", "probe", "cuts guidance", "warning", "slump")


def _vader_score(text: str) -> float:
    t = (text or "").lower()
    base = _vader.polarity_scores(t)["compound"]
    nudge = 0.0
    if any(k in t for k in _POS_KW):
        nudge += 0.3
    if any(k in t for k in _NEG_KW):
        nudge -= 0.3
    return max(-1.0, min(1.0, base + nudge))


def _finbert_batch(texts):
    """
    Call FinBERT once for a batch. Returns list of compound scores in [-1, 1]
    (P(positive) − P(negative)), or None on any failure so the caller can fall back.
    """
    if not _HF_TOKEN or not texts:
        return None
    try:
        resp = requests.post(
            _HF_URL,
            headers={"Authorization": f"Bearer {_HF_TOKEN}"},
            json={"inputs": texts, "options": {"wait_for_model": False}},
            timeout=_HF_TIMEOUT,
        )
        if resp.status_code != 200:
            return None  # 503 = model cold-loading → fall back this round
        data = resp.json()
        # Expected: list (per input) of lists of {label, score}
        if not isinstance(data, list):
            return None
        scores = []
        for entry in data:
            if isinstance(entry, dict):  # single-input shape
                entry = [entry]
            pos = neg = 0.0
            for item in entry:
                label = str(item.get("label", "")).lower()
                s = float(item.get("score", 0.0))
                if label == "positive":
                    pos = s
                elif label == "negative":
                    neg = s
            scores.append(max(-1.0, min(1.0, pos - neg)))
        return scores if len(scores) == len(texts) else None
    except Exception:
        return None


def batch_sentiment(titles):
    """
    Score a list of headline titles. Returns {title: compound_score in [-1, 1]}.
    Uses FinBERT for the whole batch when available, else VADER per title.
    Results are cached across calls to avoid rescoring repeated headlines.
    """
    titles = [t for t in (titles or []) if t]
    out, to_score = {}, []
    for t in titles:
        if t in _cache:
            out[t] = _cache[t]
        else:
            to_score.append(t)

    if to_score:
        finbert = None
        # Chunk to bounded batch sizes
        scored_all = []
        ok = True
        for i in range(0, len(to_score), _MAX_BATCH):
            chunk = to_score[i:i + _MAX_BATCH]
            res = _finbert_batch(chunk)
            if res is None:
                ok = False
                break
            scored_all.extend(res)
        finbert = scored_all if ok else None

        for idx, t in enumerate(to_score):
            score = finbert[idx] if (finbert is not None and idx < len(finbert)) else _vader_score(t)
            _cache[t] = score
            out[t] = score

    return out


def engine_name() -> str:
    return "FinBERT (HF Inference API)" if _HF_TOKEN else "VADER (lexicon fallback)"
