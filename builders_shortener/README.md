# builders-shortener

Standalone Cloud Run URL shortener for `a.buildersclub.co.kr`.

Why separate from `a.retn.kr`:

- Keeps Coupang Partners affiliate traffic/costs/logs separate from bootcamp/customer operations.
- Uses its own GCP project, Cloud Run service, Firestore collection, IAM, and billing labels.
- Only accepts approved operational target domains by default, including Toss receipt URLs.

## Endpoints

- `GET /healthz`
- `GET /s/{slug}` redirects to the stored target URL.
- `POST /internal/v1/short-links` creates/reuses a short URL.

Request:

```bash
curl -X POST "https://a.buildersclub.co.kr/internal/v1/short-links" \
  -H "Authorization: Bearer $BUILDERS_SHORTENER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://dashboard.tosspayments.com/receipt/redirection?transactionId=...&ref=PX"}'
```

Response:

```json
{"ok": true, "shortUrl": "https://a.buildersclub.co.kr/s/AbC123x"}
```

## Recommended GCP setup

Use a dedicated project, for example `builders-club-shortener` or another available ID.

Required APIs:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com --project "$PROJECT_ID"
```

Create Firestore in Native mode once in the target region/project.

Deploy:

```bash
export PROJECT_ID=builders-club-shortener
export REGION=asia-northeast3
gcloud secrets create builders-shortener-api-tokens --project "$PROJECT_ID" --replication-policy automatic
printf '%s' '<secret-token>' | gcloud secrets versions add builders-shortener-api-tokens --project "$PROJECT_ID" --data-file=-
./scripts/deploy_builders_shortener.sh
```

Then map `a.buildersclub.co.kr` to the Cloud Run service and add the DNS records requested by Cloud Run.
