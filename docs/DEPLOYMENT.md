# Deployment Guide

Production stack: **Vercel** (frontend) + **Render** (backend) + **Firebase** (auth & Firestore). All three have free tiers sufficient to run SAMPADA.ai.

---

## Step 0 — Push to GitHub

```bash
git remote add origin https://github.com/<you>/sampada.git
git push -u origin master
```

The repo is safe to push: [`.gitignore`](../.gitignore) excludes `backend/.env`, the Firebase service-account JSON, `venv/`, and `node_modules/`.

---

## Step 1 — Deploy the backend (Render)

1. [render.com](https://render.com) → **New → Web Service** → connect the GitHub repo.
2. Configure:
   - **Root Directory:** `backend`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free
3. **Environment variables:**

   | Key | Value |
   |-----|-------|
   | `FIREBASE_SERVICE_ACCOUNT_JSON` | Paste the **entire contents** of your `sampada-…firebase-adminsdk-….json` file (the whole JSON object, on one line is fine). |
   | `ALLOWED_ORIGINS` | Your Vercel URL (filled in after Step 2), e.g. `https://sampada-xi.vercel.app`. |
   | `ALPHA_VANTAGE_KEY` | Optional — only for CPI inflation. |

4. Deploy. Note your service URL (e.g. `https://sampada-xxxx.onrender.com`).

> The repo includes [`render.yaml`](../render.yaml) describing this service; you can use Render's Blueprint flow instead of manual setup if you prefer.

---

## Step 2 — Deploy the frontend (Vercel)

1. [vercel.com](https://vercel.com) → **New Project** → import the repo.
2. Configure:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Create React App (auto-detected)
3. **Environment variables:**

   | Key | Value |
   |-----|-------|
   | `REACT_APP_API_URL` | Your Render backend URL from Step 1. |
   | `REACT_APP_FIREBASE_API_KEY` | From Firebase Console → Project Settings → Web App. |
   | `REACT_APP_FIREBASE_AUTH_DOMAIN` | ″ |
   | `REACT_APP_FIREBASE_PROJECT_ID` | ″ |
   | `REACT_APP_FIREBASE_STORAGE_BUCKET` | ″ |
   | `REACT_APP_FIREBASE_MESSAGING_SENDER_ID` | ″ |
   | `REACT_APP_FIREBASE_APP_ID` | ″ |

4. Deploy. Note your app URL (e.g. `https://sampada-xi.vercel.app`).

---

## Step 3 — Wire the two together

1. **Render → Environment:** set `ALLOWED_ORIGINS` to your Vercel URL (comma-separate multiples; include `http://localhost:3000` if you still want local dev to hit prod). Save → Render auto-redeploys.
2. **Firebase Console → Authentication → Settings → Authorized domains:** add your Vercel domain (e.g. `sampada-xi.vercel.app`). Without this, login is rejected.

---

## Step 4 — Deploy Firestore security rules

The rules in [`firestore.rules`](../firestore.rules) do **not** deploy with the app. Until you publish them, watchlist writes may be rejected.

- **Console:** Firestore Database → Rules → paste the file contents → **Publish**, **or**
- **CLI:** `firebase deploy --only firestore:rules`

---

## Step 5 — Verify

1. Open the Vercel URL, sign up / log in.
2. Search a ticker (e.g. `NVDA`). You should get a full dashboard.
3. Click the ♥ next to the ticker → open the profile modal → confirm it appears in the watchlist.
4. Export a deck → confirm a `.pptx` downloads.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| **404 on `/api/analyze/...`** while the service is "live" | Render health check hitting `/` got a 404 and restarted the app in a loop. | Ensure `GET /` returns 200 (already implemented as a health-check route). Redeploy. |
| **CORS error in the browser console** | Vercel origin not in `ALLOWED_ORIGINS`. | Add the exact origin (scheme + host, no trailing slash) on Render and redeploy. |
| **Requests go to `localhost:8000` in production** | `REACT_APP_API_URL` not set, or set after the last build. | Set it in Vercel and **redeploy** (CRA env vars are baked in at build time). |
| **`Firebase: No credentials found` in Render logs** | `FIREBASE_SERVICE_ACCOUNT_JSON` empty or malformed. | Paste the full JSON object (not a file path) into the env var. App still runs in guest mode without it. |
| **Login works but watchlist writes fail** | Firestore rules not deployed, or domain not authorized. | Deploy `firestore.rules` (Step 4) and authorize the domain (Step 3.2). |
| **Vercel build fails on an ESLint warning** | CRA treats warnings as errors when `CI=true` (Vercel sets this). | Fix the warning locally — run `CI=true npm run build` in `frontend/` before pushing. |
| **First request after idle is slow (~30 s)** | Render free tier sleeps after 15 min of inactivity (cold start). | Upgrade to a paid instance to stay always-on, or accept the cold start. |

---

## Updating

Both Vercel and Render auto-redeploy on every push to the default branch. Just:

```bash
git add -A && git commit -m "..." && git push
```

Run `CI=true npm run build` in `frontend/` first to catch ESLint-as-error failures before they reach Vercel.
