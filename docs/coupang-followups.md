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

- [x] **P0.1 — Hosted fallback in `CoupangMcpClient`** when creds missing. _(uncommitted; pending operator commit)_
  Route `search_coupang_products` / rocket / budget / compare / recommendations / seasonal through `POST /v1/public/assist`. Goldbox + explicit bestcategories still raise `McpError` with a clear creds hint.
  _Verification:_ `python3 -m unittest -q` → 201 tests OK. `CoupangMcpHostedFallbackTests` adds 6 cases.

- [x] **P0.2 — Live smoke on production** _(uncommitted; pending operator commit)_
  `env -u COUPANG_ACCESS_KEY -u COUPANG_SECRET_KEY python3 bin/coupang_mcp.py search '무선청소기'` → returned 4 products, all with `short_url` = `https://a.retn.kr/s/...` (our affiliate short-link path).

- [x] **P0.3 — Add `coupang-mcp-fallback` to Cloud Run allowlist** _(shipped via commit `bdab89f`; deployed revision `a-retn-shortener-00050-wbc`)_
  `scripts/deploy_gcp_cloud_run.sh` now includes `coupang-mcp-fallback` in `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST`, and `_HOSTED_CLIENT_ID_DEFAULT` uses that ID for clean per-integration attribution.
  _Verification:_ deep closed-loop canary passed against `https://a.retn.kr` after deploy.

## Priority 1 — product completeness for k-skill contract

- [ ] **P1.1 — Hosted `/v1/public/assist` response field hardening**
  Ensure `recommendations[].rating`, `recommendations[].review_count`, `recommendations[].summary` populate on real Coupang searches (currently sometimes 0/empty). Required because vkehfdl1's core-4 feature list includes "리뷰 확인 / 상세 정보 확인".
  _How:_ audit `recommendation.normalize_product`, `evidence.build_evidence`, and Coupang Partners `productData` fields; surface rating/reviewCount/summary into the top-level item, not just `metadata`.
  _Verification:_ run `python3 scripts/smoke_test_hosted_backend.py --query '무선청소기 리뷰 많은 것'` and confirm ≥1 item has `review_count > 0` and non-empty summary.

- [x] **P1.2 — Reconcile public endpoints 404** _(commit `91dd09d`)_
  vkehfdl1's report split into two root causes:
  - `/v1/public/deeplinks` and `/v1/public/events`: **never existed in code**, only mis-listed in `CLAUDE.md` line 67. Public callers don't write events, and deeplink minting happens server-side inside `/v1/public/assist|search` responses. CLAUDE.md updated to drop the phantom routes and list the actual public surface (`assist`, `recommendations`, `search`, `goldbox`, `best/{category_id}`, `health`, `/s/<slug>`).
  - `/v1/events` and `/v1/admin/summary`: these **do** exist in `backend.py` but are gated by `OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES`, which is `false` on the public `a-retn-shortener` Cloud Run service by design (see `docs/openclaw-shopping-backend.md` "Production posture" + `README.md` lines 89-101). Operator traffic is meant to go through the separate `a-retn-shortener-ops` service. CLAUDE.md line 68 now states this explicitly so the next reader doesn't repeat the confusion.
  _Verification:_ live probe of `https://a.retn.kr` after the edit:
  - In-doc public routes (`/health`, `/v1/public/assist|recommendations|search|goldbox`, `/v1/public/best/100`) → 200/403, never 404. ✓
  - Removed-from-doc routes (`/v1/public/events`, `/v1/public/deeplinks`) → 404, matching code reality. ✓
  No code changes; doc-only fix. No deploy needed because the public service already serves what the new doc claims.

## Priority 2 — reliability / revenue defense

- [x] **P2.1 — Cloud Run min-instances capped for cost** — `scripts/deploy_gcp_cloud_run.sh` now defaults to `MIN_INSTANCES=0` and `MAX_INSTANCES=2`, so idle cost stays low while burst size is bounded. Raise these only for a deliberate launch window.

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
