#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-builders-club-shortener}"
REGION="${REGION:-asia-northeast3}"
SERVICE_NAME="${SERVICE_NAME:-builders-shortener}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://a.buildersclub.co.kr}"
COLLECTION="${COLLECTION:-short_links}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"
ALLOWED_TARGET_HOSTS="${ALLOWED_TARGET_HOSTS:-dashboard.tosspayments.com,api.tosspayments.com,ai.retn.kr,buildersclub.co.kr,www.buildersclub.co.kr}"
API_TOKEN_SECRET="${API_TOKEN_SECRET:-builders-shortener-api-tokens}"

# Before first deploy:
#   gcloud secrets create "$API_TOKEN_SECRET" --project "$PROJECT_ID" --replication-policy automatic
#   printf '%s' '<comma-separated-token(s)>' | gcloud secrets versions add "$API_TOKEN_SECRET" --project "$PROJECT_ID" --data-file=-

"${GCLOUD_BIN:-/opt/homebrew/bin/gcloud}" run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source builders_shortener \
  --allow-unauthenticated \
  --set-env-vars "^|^GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|BUILDERS_SHORTENER_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}|BUILDERS_SHORTENER_COLLECTION=${COLLECTION}|BUILDERS_SHORTENER_ALLOWED_TARGET_HOSTS=${ALLOWED_TARGET_HOSTS}" \
  --set-secrets "BUILDERS_SHORTENER_API_TOKENS=${API_TOKEN_SECRET}:latest" \
  --memory 256Mi \
  --cpu 1 \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --timeout 30 \
  "$@"
