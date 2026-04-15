# Remaining Work

This is the current prioritized backlog for the project.

## OpenClaw / Discord

### 1. Fix `~/.openclaw/openclaw.json` schema drift

Why it matters:
- Runtime warnings still appear
- Gateway env propagation is not clean for Discord/OpenClaw channels

Done already:
- Live backend now requires bearer auth
- `OPENCLAW_SHOPPING_API_TOKEN` and `OPENCLAW_SHOPPING_BASE_URL` were written into local OpenClaw config

Remaining:
- Re-verify Discord/OpenClaw callers against the protected backend in the live OpenClaw runtime
- Confirm channel-level use of `shopping-copilot` with the protected production URL

Done in repo:
- Thin clients now accept both singular/plural auth token env forms
- Thin clients now accept compatible backend URL env aliases to reduce schema drift at the repo boundary

## Stability

### 1. Move short-link storage off local sqlite

Done already:
- Firestore short-link provider now exists behind the same `UrlShortener` interface
- Built-in sqlite shortener remains available for local development
- Backend summary now reads short-link counts from the active shortener provider

Target:
- Deploy production with `OPENCLAW_SHOPPING_SHORTENER=firestore`
- Stateless redirect path `GET /s/<slug>`

Remaining:
- Re-deploy the latest hardened revision with Firestore mode enabled
- Decide whether to keep legacy sqlite short-links for local-only usage forever or mark them explicitly dev-only

### 2. Move analytics off local sqlite (or split hot/cold storage)

Done already:
- Firestore analytics provider now exists behind env-based provider selection
- Local sqlite analytics remains available for development and reversibility
- Recommendation requests now fail soft if analytics persistence temporarily breaks

Target:
- Run production with `OPENCLAW_SHOPPING_ANALYTICS_PROVIDER=firestore`

Remaining:
- Observe Firestore analytics collections under real traffic and confirm no permission or quota surprises
- Decide whether to keep sqlite analytics as local-only or use it as explicit fallback mode only

### 3. Add stronger abuse controls

Done already:
- Bearer auth support
- In-process rate limiting
- Deeplink host allowlist
- Payload length guardrails

Remaining:
- Per-client quotas
- Stricter rate-limit buckets for public traffic
- Optional allowlist mode for known OpenClaw/Discord clients

Done in repo:
- Optional client allowlist mode via `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED`
- Optional separate public/auth/admin rate-limit buckets via env-specific overrides

### 4. Improve recommendation quality

Done already:
- Vase query false-positive filter for plant/flower item leakage

Remaining:
- Category-specific exclusion maps
- Confidence scoring for weak metadata-only results
- Better fallback behavior when evidence is sparse

Done in repo:
- Category-specific exclusion maps now cover vase and vacuum leakage cases
- Recommendation evidence now emits confidence and adds conservative low-confidence summaries

## Deployment

### 1. Keep production deployment reproducible

Current production:
- Cloud Run service `a-retn-shortener`
- Region `us-central1`
- Public short base `https://a.retn.kr`

Done already:
- Custom domain mapping created
- Cloudflare DNS for `a.retn.kr` added
- Live service emits `https://a.retn.kr/s/...`

Remaining:
- Ensure latest hardened revision stays deployed after future changes
- Keep Cloud Build / Artifact Registry permissions documented
- If source deploy fails again, use the image-build fallback path

### 2. Remove local secret convenience paths from normal operator workflow

Current risk:
- Local scripts can still encourage convenience-based secret handling

Target:
- Production operator docs should point to Secret Manager-first usage only
- Local dev docs should be clearly separated from production runbooks

## Suggested Order

1. OpenClaw config cleanup
2. Firestore migration for short-links
3. Analytics shared-store migration
4. Stricter auth / quota policy
5. Recommendation quality improvements
6. Production docs cleanup
