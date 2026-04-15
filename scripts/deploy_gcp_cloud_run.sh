#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-retn-kr-website}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-a-retn-shortener}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://a.retn.kr}"

gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --allow-unauthenticated \
  --set-secrets "COUPANG_ACCESS_KEY=coupang-access-key:latest,COUPANG_SECRET_KEY=coupang-secret-key:latest,OPENCLAW_SHOPPING_API_TOKENS=openclaw-shopping-api-token:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},OPENCLAW_SHOPPING_SHORTENER=firestore,OPENCLAW_SHORT_LINKS_COLLECTION=short_links,OPENCLAW_SHOPPING_ANALYTICS_PROVIDER=firestore,OPENCLAW_ANALYTICS_COLLECTION_PREFIX=shopping,OPENCLAW_SHOPPING_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}" \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 1 \
  --timeout 60 \
  "$@"
