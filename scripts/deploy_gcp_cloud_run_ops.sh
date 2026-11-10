#!/usr/bin/env bash
set -euo pipefail
RUN_SMOKE_TEST_AFTER_DEPLOY=false
RUN_CLOSED_LOOP_AFTER_DEPLOY=false
RUN_DEEP_AGENT_CANARY_AFTER_DEPLOY=false
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ "$PWD" == "$ROOT" ]] || { echo "run from $ROOT" >&2; exit 2; }
PROJECT_ID=""; ENABLE=false; CLIENT=""; CHANGE_SHA=""; SOURCE_SHA=""
while (($#)); do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2;;
    --enable-book-campaign-routes) ENABLE=true; shift;;
    --allow-client) CLIENT="$2"; shift 2;;
    --change-sha) CHANGE_SHA="$2"; shift 2;;
    --source-manifest-sha) SOURCE_SHA="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[[ -n "$PROJECT_ID" && "$ENABLE" == true && -n "$CLIENT" && "$CHANGE_SHA" =~ ^[0-9a-f]{64}$ && "$SOURCE_SHA" =~ ^[0-9a-f]{64}$ ]] || exit 2
python3 scripts/compute_change_manifest.py --root "$ROOT" --check-gcloudignore
upload=$(mktemp); staged=$(mktemp); trap 'rm -f "$upload" "$staged"' EXIT
python3 scripts/compute_change_manifest.py --root "$ROOT" --mode upload > "$upload"
[[ "$(shasum -a 256 "$upload" | awk '{print $1}')" == "$SOURCE_SHA" ]] || { echo source-manifest-sha-mismatch >&2; exit 3; }
gcloud meta list-files-for-upload --source "$ROOT" > "$staged"
python3 scripts/compute_change_manifest.py --root "$ROOT" --verify-staged-file-list "$staged"
ALLOWLIST="insta-cardnews-book-sales-v1,$CLIENT"
gcloud run deploy a-retn-shortener-ops --project "$PROJECT_ID" --region us-central1 --source "$ROOT" --no-allow-unauthenticated \
  --set-secrets "COUPANG_ACCESS_KEY=coupang-access-key:latest,COUPANG_SECRET_KEY=coupang-secret-key:latest,OPENCLAW_SHOPPING_API_TOKENS=openclaw-shopping-api-token:latest" \
  --set-env-vars "^|^GOOGLE_CLOUD_PROJECT=$PROJECT_ID|OPENCLAW_SHOPPING_SHORTENER=firestore|OPENCLAW_SHOPPING_ANALYTICS_PROVIDER=firestore|OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES=false|OPENCLAW_SHOPPING_ENABLE_BOOK_CAMPAIGN_ROUTES=true|OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED=true|OPENCLAW_SHOPPING_CLIENT_ALLOWLIST=$ALLOWLIST|BOOK_CAMPAIGN_CHANGE_SHA=$CHANGE_SHA|BOOK_CAMPAIGN_SOURCE_MANIFEST_SHA=$SOURCE_SHA"
