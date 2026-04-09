# OpenClaw Shopping Skill Package

This directory contains the public-facing documentation for the hosted OpenClaw shopping skill.

## Purpose

The skill is intentionally thin:

- it collects a shopping request from the user
- it forwards that request to an operator-hosted backend
- it returns ranked recommendations, rationale, caveats, and deeplinks

The skill does **not** own Coupang affiliate credentials.

## What the operator must provide

- a hosted backend URL
- backend-side Coupang credentials
- affiliate disclosure in the final user-facing surface where links are published

## Suggested backend contract

The skill expects the hosted backend to expose:

- `GET /healthz`
- `POST /v1/assist`
- `POST /v1/events`
- `GET /v1/admin/summary` (operator-only)

## Recommended runtime configuration

The examples in `SKILL.md` use:

- `OPENCLAW_SHOPPING_BASE_URL` — base URL for the hosted backend
- `OPENCLAW_SHOPPING_TIMEOUT_SECONDS` — optional client timeout override

If the eventual CLI or runtime uses different names, update this directory and the backend docs together.

## User experience contract

A good shopping run should capture:

- user intent (what they want to buy)
- important constraints (budget, form factor, category, exclusions)
- optional evidence the user wants considered

A good response should return:

- a short ranked list
- why each item made the list
- known tradeoffs or risks
- purchase/deeplink URLs

## Disclosure and compliance notes

- Never ask the user for backend secrets or affiliate keys.
- Make affiliate relationships visible where recommendations are shown.
- Do not claim that reviews or evidence were analyzed unless they were actually provided to the backend.
