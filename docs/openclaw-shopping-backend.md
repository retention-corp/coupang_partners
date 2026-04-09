# OpenClaw Shopping Backend Guide

This document captures the approved hosted-backend shape for the OpenClaw shopping flow so implementation, operations, and public-skill packaging stay aligned.

## Why the backend is hosted

The OpenClaw skill is intended to be public and forkable, but the recommendation and analytics loop should remain centralized:

- Coupang affiliate secrets stay server-side.
- Recommendation quality can improve as the operator stores intent, evidence, and outcome data.
- Public skill users get an agentic shopping experience without direct access to backend credentials.

## Request/response contract

### `POST /v1/assist`

Expected input:

- natural-language shopping query
- optional budget/category constraints
- optional evidence snippets, HTML snapshots, or operator-supplied notes

Expected output:

- ranked recommendations
- grounded rationale for each recommendation
- explicit risks/caveats
- deeplinks suitable for affiliate attribution

### `POST /v1/events`

Expected event types include:

- `result_viewed`
- `deeplink_clicked`
- future outcome signals such as conversion-related feedback

### `GET /v1/admin/summary`

Operators should be able to inspect:

- query volume
- recommendation volume
- click activity
- evidence-ingestion counts
- basic recent usage summaries

## Data model guidance

The approved MVP uses sqlite for a reversible standard-library implementation.

Suggested tables from the PRD:

- `queries`
- `recommendations`
- `events`
- `evidence_snippets`

Store enough context to improve later ranking without storing unnecessary secrets or opaque blobs by default.

## Evidence policy

Evidence should stay conservative and auditable.

Good evidence sources:

- product metadata returned by Coupang
- user-supplied snippets
- operator-reviewed HTML snapshots
- prior interaction/outcome summaries already captured by the backend

Avoid implying that the system has reviewed hidden or unavailable data when it only has weak evidence.

## Public skill boundary

The public skill package should never contain:

- `COUPANG_ACCESS_KEY`
- `COUPANG_SECRET_KEY`
- embedded backend operator secrets

The public skill package should contain:

- the hosted backend contract
- user-visible prompts for query/budget/preferences
- clear disclosure that recommendations come from a hosted operator backend

## Operator checklist

1. Configure backend-only Coupang credentials.
2. Publish the backend at a stable base URL.
3. Expose only the documented public endpoints.
4. Add affiliate disclosure anywhere recommendations are published to end users.
5. Review evidence ingestion and logging retention before broad rollout.

## Manual smoke test checklist

1. Start the backend locally or in a staging environment.
2. Call `GET /healthz` and confirm machine-readable JSON.
3. Submit a sample `POST /v1/assist` request with a realistic shopping query.
4. Confirm the response includes recommendations, rationale, risks, and deeplinks.
5. Emit a sample `POST /v1/events` payload.
6. Inspect sqlite-backed admin summary output.
7. Verify the public skill docs do not reveal secrets.
