# Book recommendation + Coupang Partners integration notes

## Source of truth

Attach all new book recommendation + affiliate flow work to:

- `/Users/gyusupsim/Projects/products/coupang_partners`

Reason:
- existing hosted backend already lives here
- recommendation/evidence/ranking modules already exist here
- OpenClaw skill and GCP deploy scripts already exist here
- this is the natural place to add a book vertical instead of creating a separate backend

## What was imported

Imported the standalone `kbook-reco` MVP into:

- `book_reco/`

This includes:
- Naver search provider
- Data4Library provider
- NLK metadata provider
- fallback recommendation logic
- service layer
- CLI and MCP server entrypoints

Reference docs copied into:
- `docs/book-reco/README-kbook-reco.md`
- `docs/book-reco/env.example.kbook`

## Recommended next architecture

1. Keep generic shopping backend as the main API surface.
2. Add a `book_vertical` or `book_reco` integration layer inside this repo.
3. Use Notion reading history as preference/context input.
4. Query Data4Library / Naver / NLK for candidate books.
5. Exclude books already present in Notion Books DB.
6. Map final candidate books to Coupang products.
7. Do not rely on ISBN-only Coupang lookup; validate with title/author/category.

## New external data source pending

The operator requested access to:
- 문화체육관광부 국립중앙도서관_사서추천도서

When approved, add it as another provider feeding Korean curator-quality recommendations.
