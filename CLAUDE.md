# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project shape

A hosted Coupang Partners shopping backend plus public OpenClaw skill packages. One production surface (`https://a.retn.kr`, Cloud Run service `a-retn-shortener`) owns credentials, analytics, ranking, and short-link state. Public callers go through tokenless paths; operator/admin flows require a bearer token.

Python 3.11, **standard-library only** at runtime (`requirements.txt` is intentionally empty). Tests use `unittest`. Firestore is an optional persistence backend reached via env flags — do not add hard dependencies on it.

## Common commands

```bash
# Full test suite (uses unittest discovery from repo root)
python3 -m unittest -q

# Run a single test module / class / method
python3 -m unittest -q test_backend
python3 -m unittest -q test_backend.BackendTestCase
python3 -m unittest -q test_backend.BackendTestCase.test_assist_happy_path

# Operator-only local backend (requires real Coupang keys)
export COUPANG_ACCESS_KEY=...  COUPANG_SECRET_KEY=...
export OPENCLAW_SHOPPING_API_TOKENS="long-random-token"
export OPENCLAW_SHOPPING_DB_PATH=".data/openclaw-shopping.sqlite3"
python3 backend.py                       # or: scripts/run_openclaw_backend.fish

# Thin CLI bridge against hosted backend (no secrets needed)
python3 bin/openclaw_shopping.py "30만원 이하 무선청소기, 원룸용"

# Smoke-test the live hosted backend
python3 scripts/smoke_test_hosted_backend.py

# Deploy to Cloud Run (service a-retn-shortener, us-central1)
scripts/deploy_gcp_cloud_run.sh
```

## Architecture

### Module layout is flat at the repo root — the `coupang_partners/` package is a re-export shim

`backend.py`, `client.py`, `recommendation.py`, `analytics.py`, `evidence.py`, `economics.py`, `product_page_evidence.py`, `security.py`, `url_shortener.py` all live **at the repo root**. The `coupang_partners/` directory is a thin compatibility package whose `__init__.py` re-imports from the root modules. When you edit a module, edit the root file, not a copy. Imports like `from backend import ...` and `from coupang_partners import ShoppingBackend` both work.

### Request flow (`POST /v1/public/assist`)

1. `security.validate_payload_limits` + rate limit (public/auth/admin buckets).
2. `recommendation.normalize_request` → `recommendation.build_search_queries` plans queries.
3. `backend.ShoppingBackend._search_products` calls `client.CoupangPartnersClient` (HMAC-SHA256 over `signed-date + METHOD + path + raw_querystring`).
4. `product_page_evidence.enrich_products_with_page_evidence` scrapes landing-page signals (best-effort, time-boxed).
5. `recommendation.recommend_products` applies category exclusion maps, must-have token rules, extremum-intent sorting (`_EXTREMUM_RULES` handles 제일 긴 / 최저가 / 평점 / 리뷰), and evidence grounding from `evidence.build_evidence`.
6. `url_shortener` attaches `short_deeplink`; `analytics.AnalyticsStore` logs query/recommendation/evidence counts.

`DISCLOSURE_TEXT` ("파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음") must stay in every response that surfaces affiliate links. `impressionUrl` from Reco v2 fires only on real user visibility.

### Provider swapping is env-driven

Both analytics and short-links switch between sqlite (dev) and Firestore (Cloud Run) purely via env:

- `OPENCLAW_SHOPPING_SHORTENER=builtin|firestore`
- `OPENCLAW_SHOPPING_ANALYTICS_PROVIDER=sqlite|firestore`
- `GOOGLE_CLOUD_PROJECT`, `OPENCLAW_SHORT_LINKS_COLLECTION`, `OPENCLAW_ANALYTICS_COLLECTION_PREFIX`, `FIRESTORE_EMULATOR_HOST`

If Firestore short-link generation fails, the backend falls back to the raw affiliate URL rather than dropping the request — preserve that behavior.

### Public vs internal endpoint split

- Public, tokenless: `GET /health`, `POST /v1/public/assist`, `POST /v1/public/events`, `POST /v1/public/deeplinks`, `GET /s/<slug>`.
- Bearer-auth required: `POST /internal/v1/assist|events|deeplinks`, `GET /v1/admin/summary`.
- Optional hardening: `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED=true` + `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST=...`; separate `RATE_LIMIT_{REQUESTS,WINDOW_SECONDS}_{PUBLIC,AUTH,ADMIN}` env vars.
- Deeplink host allowlist is enforced in `security.validate_deeplink_url` (`coupang.com`, `link.coupang.com`, `www.coupang.com`).

### Skill packages are public-facing clients, not backend code

`openclaw_skill/` (shopping-copilot), `coupang_product_search_skill/`, and `bin/openclaw_shopping.py` all default to `https://a.retn.kr`. Closed-beta paths are **pinned** to production — non-prod overrides require `OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND=true`. Operator internal calls require `OPENCLAW_SHOPPING_USE_INTERNAL_API=true` plus `OPENCLAW_SHOPPING_API_TOKEN(S)`. Thin clients accept both singular/plural token env names and several URL aliases (`OPENCLAW_SHOPPING_BASE_URL`, `OPENCLAW_SHOPPING_BACKEND_URL`, `SHOPPING_COPILOT_BASE_URL`) to absorb config drift.

The skill's `SKILL.md` trigger list (shopping intents, goldbox/best/rocket phrases, taste-sensitive requests) is load-bearing — the OpenClaw router reads those keywords. Updates to supported commands must keep `SKILL.md`, `scripts/openclaw-shopping-skill.py`, and `backend.py` in sync; see `docs/FILE-CHAINING.md` Chain 1.

### Book vertical lives in `book_reco/`

Imported from the standalone `kbook-reco` MVP. Three-layer shape: providers (Naver / Data4Library / NLK + deterministic fallback) → services → CLI + MCP server entrypoints. Env keys: `NAVER_CLIENT_ID/SECRET`, `DATA4LIBRARY_API_KEY`, `NLK_API_KEY`. Per `docs/book-reco/INTEGRATION-NOTES.md`, the book vertical should eventually map recommended books to Coupang products; **do not rely on ISBN-only Coupang lookup** — validate with title/author/category.

## Hard rules

- Never commit real `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`, or operator bearer tokens. `.env.local` is local-only convenience; treat it as untrusted for anything shared.
- Public skill packages must never embed backend/Coupang secrets. Keep affiliate deeplink creation server-side.
- Prefer Reco v2 for new work; v1 is a thin compatibility wrapper only.
- Evidence must degrade gracefully — never claim review/detail analysis when only metadata is available. Low-confidence summaries stay explicitly conservative (see `evidence.py` and the `recommendation` confidence plumbing).
- When changing routing, auth, or skill commands, update the matching docs in `openclaw_skill/`, `coupang_product_search_skill/`, and `docs/` in the same change.

## Navigation shortcuts

- Task routing by area: `docs/FILE-CHAINING.md` (OpenClaw/Discord, Stability, Deployment chains).
- Current backlog: `docs/REMAINING-WORK.md`.
- Backend contract: `docs/openclaw-shopping-backend.md`.
- Search-skill PRD (goldbox / best / rocket / sort params): `docs/PRD-coupang-search-skill.md`.
- Korean README + OpenClaw install guide: `README.ko.md`, `OPENCLAW-INSTALL.ko.md`.
