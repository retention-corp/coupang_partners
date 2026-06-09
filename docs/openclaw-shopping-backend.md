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
- direct shopping search intents such as "제일 긴", "최저가", "평점 높은", and "리뷰 많은"

Expected output:

- ranked recommendations
- comparison-aware ranking when the query asks for the longest, cheapest, highest-rated, or most-reviewed item
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

Production posture:

- Keep operator routes disabled by default on the public Cloud Run service.
- Run a separate operator Cloud Run service for stable internal/admin traffic.
- Only enable public-service operator routes temporarily for an authenticated validation window, then redeploy with them disabled again.

## CLI bridge

This repo also includes a thin CLI entrypoint at `bin/openclaw_shopping.py` for local smoke tests and scripted calls into the hosted backend.

Recommended environment variables:

- `OPENCLAW_SHOPPING_BASE_URL`
- `OPENCLAW_SHOPPING_OPERATOR_BASE_URL`
- `OPENCLAW_SHOPPING_TIMEOUT_SECONDS`

## Stable split-service production setup

Public service:

```bash
bash scripts/deploy_gcp_cloud_run.sh
```

Operator service:

```bash
bash scripts/deploy_gcp_cloud_run_operator.sh
```

Operator clients should use the operator service URL for internal calls:

```bash
export OPENCLAW_SHOPPING_OPERATOR_BASE_URL="$(gcloud run services describe a-retn-shortener-ops --project retn-kr-website --region us-central1 --format='value(status.url)')"
export OPENCLAW_SHOPPING_USE_INTERNAL_API="true"
```
