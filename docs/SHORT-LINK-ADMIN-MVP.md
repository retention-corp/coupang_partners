# Short-Link Admin MVP

This MVP adds a protected semi-private admin surface for the existing
`a.retn.kr` short-link service. It is operator-facing only. Public/OpenClaw
callers keep using `short_deeplink` and `GET /s/<slug>`.

## Runtime Boundary

- Admin UI: `GET /admin` after login.
- Login API: `POST /admin/api/session` with `username` and `password`.
- Link API: `GET` and `POST /admin/api/short-links`, plus
  `PATCH /admin/api/short-links/<slug>` for lifecycle changes.
- User API: `GET` and `POST /admin/api/users`, plus `PATCH`/`DELETE` for a
  specific username.
- Audit API: `GET /admin/api/audit?limit=100`.

The public redirect contract stays unchanged: `GET /s/<slug>` returns a redirect
for active safe links and returns not-found for inactive or unsafe links. Existing
Firestore/sqlite records remain readable as legacy or unassigned records. They are
not migrated to a user automatically.

## Local Operator Setup

Use local-only credentials for development:

```bash
export OPENCLAW_SHOPPING_SHORTENER=builtin
export OPENCLAW_SHOPPING_PUBLIC_BASE_URL=http://127.0.0.1:8080
export OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES=true
export BUILDERS_SHORTENER_BOOTSTRAP_ADMIN_USERNAME=owner@example.com
export BUILDERS_SHORTENER_BOOTSTRAP_ADMIN_PASSWORD='replace-with-local-password'
export BUILDERS_SHORTENER_ADMIN_STATE_PATH=.data/admin-state.json
export BUILDERS_SHORTENER_ADMIN_AUDIT_PATH=.data/admin-audit.jsonl
python3 backend.py
```

Do not store real Coupang keys, bearer tokens, or production admin passwords in
the repository. Production credentials belong in Secret Manager or 1Password.

## MVP Behavior

- Approved owner/admin users can create one link or a batch of rows.
- Admin accounts have explicit `pending`, `approved`, `rejected`, or
  `suspended` status. Only `approved` accounts can create or resolve an admin
  session, and status changes retain a per-user history.
- Alias policy is global: lowercase letters, numbers, and hyphens, 3 to 64
  characters. Reserved values such as `admin`, `api`, `docs`, `health`, `login`,
  and `s` are rejected before persistence.
- Duplicate target URLs are allowed in the admin creation path.
- Deactivation preserves the stored link and click history, but public redirect
  resolution returns not-found.
- Viewer accounts can read allowed admin data but cannot create, update, or
  deactivate links.
- The system rejects any role/status change or deletion that would leave no
  approved owner/admin account.
- Audit events record actor, action, target, outcome, timestamp, and small
  metadata for session, user, and short-link mutations.

## Verification

Focused MVP regression checks:

```bash
python3 -m unittest test_builders_admin_auth test_url_shortener test_backend.AdminShortLinkIntegrationTests
```

Full repository verification:

```bash
python3 -m unittest discover
```

The focused command covers protected access, roles, audit logging, alias
uniqueness, batch row errors, inactive redirect behavior, legacy record listing,
and the admin API/UI integration. The full command covers the broader hosted
shopping backend, analytics, agent, and documentation-facing behavior.

## Future Extension Boundaries

The following items are intentional extension points, not active MVP features:

- UTM automation: the admin API does not append or manage campaign parameters.
  Operators must include any desired parameters in the target URL explicitly.
- Detailed analytics: the MVP stores link click counts and audit events, but it
  does not add per-link dashboards, conversion reporting, or cohort analysis.
- Multiple short-link domains: the active short URL always uses the configured
  `OPENCLAW_SHOPPING_PUBLIC_BASE_URL`. Row-level `domain` fields are ignored by
  the MVP and must not be treated as enabled configuration.
- Production identity store: the current JSON admin store is suitable for local
  MVP operation. Production should swap in durable shared storage before relying
  on it across instances.
