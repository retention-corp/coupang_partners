#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-retn-kr-website}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-a-retn-shortener}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://a.retn.kr}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"
RUN_SMOKE_TEST_AFTER_DEPLOY="${RUN_SMOKE_TEST_AFTER_DEPLOY:-true}"
SMOKE_TEST_REQUIRE_AUTH="${SMOKE_TEST_REQUIRE_AUTH:-false}"
RUN_CLOSED_LOOP_AFTER_DEPLOY="${RUN_CLOSED_LOOP_AFTER_DEPLOY:-true}"
RUN_DEEP_AGENT_CANARY_AFTER_DEPLOY="${RUN_DEEP_AGENT_CANARY_AFTER_DEPLOY:-false}"
AGENT_CLOSED_LOOP_STATE_FILE="${AGENT_CLOSED_LOOP_STATE_FILE:-/tmp/a-retn-agent-closed-loop-state.json}"
AGENT_MIN_DEEP_INTERVAL_SECONDS="${AGENT_MIN_DEEP_INTERVAL_SECONDS:-3600}"
PUBLIC_RATE_LIMIT_REQUESTS="${PUBLIC_RATE_LIMIT_REQUESTS:-12}"
PUBLIC_RATE_LIMIT_WINDOW_SECONDS="${PUBLIC_RATE_LIMIT_WINDOW_SECONDS:-60}"
RESPONSE_CACHE_TTL_SECONDS="${RESPONSE_CACHE_TTL_SECONDS:-900}"
ALLOWED_CLIENT_IDS="${ALLOWED_CLIENT_IDS:-openclaw-skill,openclaw-skill-*,local-cli,smoke-test,agent-smoke,closed-loop-monitor,github-actions-agent-ops,coupang-mcp-fallback,hermes-agent,codex,claude-code-skill,chatgpt-gpt,claw-*}"
CHANGE_SHA=""; SOURCE_MANIFEST_SHA=""; EXTRA_ARGS=()
while (($#)); do
  case "$1" in
    --change-sha) CHANGE_SHA="$2"; shift 2;;
    --source-manifest-sha) SOURCE_MANIFEST_SHA="$2"; shift 2;;
    *) EXTRA_ARGS+=("$1"); shift;;
  esac
done
[[ -z "$CHANGE_SHA" || "$CHANGE_SHA" =~ ^[0-9a-f]{64}$ ]] || exit 2
[[ -z "$SOURCE_MANIFEST_SHA" || "$SOURCE_MANIFEST_SHA" =~ ^[0-9a-f]{64}$ ]] || exit 2
ROOT=$(cd "$(dirname "$0")/.." && pwd); [[ "$PWD" == "$ROOT" ]] || exit 2
python3 scripts/compute_change_manifest.py --root "$ROOT" --check-gcloudignore
HASH_ENV="|BOOK_CAMPAIGN_CHANGE_SHA=${CHANGE_SHA}|BOOK_CAMPAIGN_SOURCE_MANIFEST_SHA=${SOURCE_MANIFEST_SHA}"
upload=$(mktemp); staged=$(mktemp); trap 'rm -f "$upload" "$staged"' EXIT
python3 scripts/compute_change_manifest.py --root "$ROOT" --mode upload > "$upload"
if [[ -n "$SOURCE_MANIFEST_SHA" ]]; then [[ "$(shasum -a 256 "$upload" | awk '{print $1}')" == "$SOURCE_MANIFEST_SHA" ]] || exit 3; fi
gcloud meta list-files-for-upload --source "$ROOT" > "$staged"
python3 scripts/compute_change_manifest.py --root "$ROOT" --verify-staged-file-list "$staged"

gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --allow-unauthenticated \
  --set-secrets "COUPANG_ACCESS_KEY=coupang-access-key:latest,COUPANG_SECRET_KEY=coupang-secret-key:latest,OPENCLAW_SHOPPING_API_TOKENS=openclaw-shopping-api-token:latest,NLK_API_KEY=nl-go-kr-cert-key:latest,SASEO_API_KEY=nl-go-kr-cert-key:latest" \
  --set-env-vars "^|^GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|OPENCLAW_SHOPPING_SHORTENER=firestore|OPENCLAW_SHORT_LINKS_COLLECTION=short_links|OPENCLAW_SHOPPING_ANALYTICS_PROVIDER=firestore|OPENCLAW_ANALYTICS_COLLECTION_PREFIX=shopping|OPENCLAW_SHOPPING_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}|OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES=false|OPENCLAW_SHOPPING_ENABLE_BOOK_CAMPAIGN_ROUTES=false${HASH_ENV}|OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC=${PUBLIC_RATE_LIMIT_REQUESTS}|OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_PUBLIC=${PUBLIC_RATE_LIMIT_WINDOW_SECONDS}|OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED=true|OPENCLAW_SHOPPING_CLIENT_ALLOWLIST=${ALLOWED_CLIENT_IDS}|OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS=${RESPONSE_CACHE_TTL_SECONDS}" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --timeout 60 \
  "${EXTRA_ARGS[@]}"

if [[ "${RUN_SMOKE_TEST_AFTER_DEPLOY}" == "true" ]]; then
  smoke_args=(
    scripts/smoke_test_hosted_backend.py
    --base-url "${PUBLIC_BASE_URL}"
  )
  if [[ "${SMOKE_TEST_REQUIRE_AUTH}" == "true" ]]; then
    smoke_args+=(--require-auth)
  fi
  OPENCLAW_SHOPPING_CLIENT_ID="${OPENCLAW_SHOPPING_CLIENT_ID:-smoke-test}" python3 "${smoke_args[@]}"
fi

if [[ "${RUN_CLOSED_LOOP_AFTER_DEPLOY}" == "true" ]]; then
  closed_loop_args=(
    scripts/agent_closed_loop.py
    --base-url "${PUBLIC_BASE_URL}"
    --client-id "${OPENCLAW_SHOPPING_CLIENT_ID:-smoke-test}"
    --surface cli
  )
  if [[ "${RUN_DEEP_AGENT_CANARY_AFTER_DEPLOY}" == "true" ]]; then
    closed_loop_args+=(
      --deep
      --force-deep
      --state-file "${AGENT_CLOSED_LOOP_STATE_FILE}"
      --min-deep-interval-seconds "${AGENT_MIN_DEEP_INTERVAL_SECONDS}"
    )
  fi
  python3 "${closed_loop_args[@]}"
fi
