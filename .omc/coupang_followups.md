# Coupang Partners / k-skill integration follow-ups

Source of truth for the agentic follow-up loop. A weekly `/schedule` cron agent reads this, executes **one** undone item per run, updates status, and commits. Items tagged `🔒 approval-required` must be skipped by the cron and only executed when a human operator runs them manually.

## Context

- k-skill PR #140 merged to `dev`, wires their `coupang-product-search` skill to this repo's `bin/coupang_mcp.py`.
- 99% of k-skill end users have no `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY`. Without the hosted fallback their `search` command fails immediately.
- Revenue thesis: every call routed through `https://a.retn.kr/v1/public/assist` uses our HMAC signature, our affiliate tracking code, our commission.
- Primary lever: make local wrapper transparently fall back to hosted when creds missing.

## Status legend

- `[ ]` todo
- `[~]` in progress
- `[x]` done (add commit sha)
- `🔒` approval-required → cron must not self-execute
- `⏳` blocked → waiting on external signal

## Priority 0 — revenue capture (done / in flight)

- [x] **P0.1 — Hosted fallback in `CoupangMcpClient`** when creds missing. _(shipped via PR #1 merge commit `8fe96b6`)_
  Route `search_coupang_products` / rocket / budget / compare / recommendations / seasonal through `POST /v1/public/assist`. Goldbox + explicit bestcategories still raise `McpError` with a clear creds hint.
  _Verification:_ `python3 -m unittest -q` → 201 tests OK. `CoupangMcpHostedFallbackTests` adds 6 cases.

- [x] **P0.2 — Live smoke on production**
  `env -u COUPANG_ACCESS_KEY -u COUPANG_SECRET_KEY python3 bin/coupang_mcp.py search '무선청소기'` → returned 4 products, all with `short_url` = `https://a.retn.kr/s/...` (our affiliate short-link path).

- [ ] **P0.3 — Add `coupang-mcp-fallback` to Cloud Run allowlist** 🔒 approval-required
  Current prod allowlist: `openclaw-skill,local-cli,smoke-test`. Fallback currently pretends to be `openclaw-skill` which works, but this pollutes analytics. After we add `coupang-mcp-fallback` to `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST` in `scripts/deploy_gcp_cloud_run.sh` and re-deploy, flip `_HOSTED_CLIENT_ID_DEFAULT` to `coupang-mcp-fallback` for clean per-integration attribution.
  _Blocker:_ needs Cloud Run redeploy; do not self-execute.

## Priority 1 — product completeness for k-skill contract

- [~] **P1.1 — Hosted `/v1/public/assist` response field hardening** _(committed `28b2d13`; blocked on Akamai 403)_
  Ensure `recommendations[].rating`, `recommendations[].review_count`, `recommendations[].summary` populate on real Coupang searches (currently sometimes 0/empty). Required because vkehfdl1's core-4 feature list includes "리뷰 확인 / 상세 정보 확인".
  _Code changes:_ `product_page_evidence.py` now extracts `aggregateRating.ratingValue` / `reviewCount` from landing-page JSON-LD and allows `link.coupang.com` affiliate URLs through `build_product_page_url`. `recommendation.normalize_product` promotes `page_rating` / `page_review_count` to top-level `rating` / `review_count` fallbacks and synthesizes a new top-level `summary` (description → page_description → first snippet, capped at 280 chars). 206 tests OK.
  _Verification attempted:_ `scripts/verify_p1_1_hosted_fields.py "무선청소기 리뷰 많은 것"` hits prod for 3 candidates, runs each through the updated pipeline. All 3 failed `page_evidence_fetched=false` → Coupang Akamai returns **HTTP 403 Access Denied** to server-side `urllib` requests regardless of User-Agent (desktop/mobile) or target host (`link.coupang.com`, `www.coupang.com`, `m.coupang.com`). Reference `#18.b03a6f3d.1776691941.6d7b2fdd`. So the plumbing is ready but the data source upstream is blocked.
  _Blockers:_ (1) Akamai anti-bot on Coupang product pages — needs a headless-browser path (Playwright) or an alternate rating source (Naver Shopping API) before these fields can populate in prod. (2) Even after (1), P0.3 redeploy gate still applies for the code to run live.

- [ ] **P1.2 — Reconcile public endpoints 404**
  vkehfdl1 confirmed `POST /v1/public/deeplinks`, `POST /v1/public/events`, `POST /v1/events`, `GET /v1/admin/summary` return 404 on production.
  _Options (pick one):_
  1. Deploy the missing routes if they exist in code.
  2. Remove them from `README.md`, `docs/openclaw-shopping-backend.md`, and skill docs.
  Whichever path, doc and deploy MUST match.
  _Verification:_ `curl -s -o /dev/null -w "%{http_code}\n" https://a.retn.kr/v1/public/deeplinks -X POST -H "Content-Type: application/json" -d '{}'` — if kept, should be 4xx with JSON error, not 404.

## Priority 2 — reliability / revenue defense

- [x] **P2.1 — Cloud Run min-instances = 1** — already set in `scripts/deploy_gcp_cloud_run.sh:8` (`MIN_INSTANCES=1`). No action needed.

- [ ] **P2.2 — Impression-fire rate monitoring**
  Revenue = clicks × approved rate. Instrument: weekly aggregation comparing `analytics.AnalyticsStore` recommendation count vs Coupang Partners dashboard impression count. If ratio < 0.8 for a week, flag.
  _Verification_: `python3 scripts/weekly_impression_audit.py` (to be created) emits a ratio.

## Priority 3 — housekeeping

- [ ] **P3.1 — Fix `coupang_product_search_skill/scripts/openclaw-coupang-mcp.py` ImportError**
  Local `coupang_mcp_client.py` in the skill dir shadows the root module and crashes on import. Either rename the inner module, or remove it in favor of root import. PR #140 avoids this path so not urgent, but cleans first-impression for anyone poking at the repo.

- [ ] **P3.2 — Python 3.14 sqlite3 ResourceWarning cleanup**
  Low stakes cosmetic. Audit `analytics.py` / `url_shortener.py` sqlite connection lifecycles, ensure `with contextlib.closing(...)`.

## Anti-list (do NOT do)

- ❌ Resurrect the 8-tool coupang-mcp parity (rocket/budget/goldbox/best/seasonal/compare as separate endpoints). vkehfdl1 explicitly /approve'd scope reduction to search + price compare + detail + review.
- ❌ Open a PR on `NomaDamas/k-skill` to switch them from `bin/coupang_mcp.py` to `bin/openclaw_shopping.py`. vkehfdl1 just merged #140; another contract change would cost goodwill. Internal fallback accomplishes the same revenue outcome.
- ❌ Rewrite the `coupang-product-search/SKILL.md` tool list. Same reason.

## Proposed `/schedule` cron prompt (NOT yet registered)

Weekly on Monday 09:00 KST. Awaiting operator approval before `/schedule` is invoked.

```
Project: /Users/gyusupsim/Projects/products/coupang_partners

1. Read .omc/coupang_followups.md. Pick the lowest-priority item that is `[ ]` AND not `🔒` AND not `⏳`.
2. Execute it end-to-end per its own verification steps. If verification passes, mark `[x]` with the commit sha. If not, mark `[~]` with a short note and keep the item.
3. Do NOT self-execute 🔒 items. If one is next, skip and move on.
4. If no unblocked item remains, emit a short status summary and exit.
5. Stage changes and commit with message `chore(followups): advance <item-id>` — one commit per item.
6. Do not push. Operator will push after reviewing.
```

## Manual re-entry (without cron)

Claude Code session: `한국 오픈클로 유저 중 99% 는 쿠팡 어필리에이트 키가 없다. 쿠팡 통합 후속 작업 한 개만 전진시켜줘. @.omc/coupang_followups.md 에서 다음 미완료 항목을 집어서 실행하고 상태 업데이트해.`
