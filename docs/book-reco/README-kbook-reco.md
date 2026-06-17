# kbook-reco

A minimal Korean book recommendation package for CLI, MCP, and LLM tool-calling workflows.

## What it does

`kbook-reco` searches Korean books, recommends related books, describes a book by ISBN13, and returns trending books. It is designed for South Korea first, uses Korean-oriented providers when credentials are available, and still works in fallback mode with no API keys.

## Architecture

The package has three layers:

1. **Providers**: thin adapters for external APIs or local fallback logic.
2. **Services**: orchestration layer that composes providers, applies fallback, and normalizes outputs.
3. **Interfaces**: Typer CLI and MCP server.

Default provider order:

- Search: Naver Book Search API
- Recommendation: Data4Library recommendation/popular loan APIs
- Metadata: National Library of Korea if configured, else search provider metadata
- Fallback: deterministic related-book ranking using search results

## Ranking formula for fallback recommendations

Fallback recommendations score each candidate with a deterministic weighted sum:

- category match: `+4.0`
- title keyword overlap: `+1.5 * overlap_count`
- same author: `+3.0`
- same publisher: `+1.5`
- Korean text preference: `+1.0`
- popularity contribution: `+min(popularity_score, 100) / 100`

Candidates with the same ISBN13 as the seed book are removed. Results are sorted by score descending, then title ascending.

## Supported commands

- `kbook search "<query>"`
- `kbook recommend --isbn <isbn13>`
- `kbook recommend --query "<book or topic>"`
- `kbook trending`
- `kbook describe --isbn <isbn13>`

All commands support `--json`.

## Environment variables

See `.env.example`.

Primary variables:

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `DATA4LIBRARY_API_KEY`
- `NLK_API_KEY`
- `KBOOK_TIMEOUT_SECONDS`
- `KBOOK_MAX_RESULTS`
- `KBOOK_USER_AGENT`
- `KBOOK_LOG_LEVEL`

## Provider notes

### Naver
Used for Korean book search. Best default search provider for MVP.

### Data4Library
Used for recommendations and trending books when API access is available. Also supports book detail lookups in some flows.

### National Library of Korea (NLK)
Used as optional metadata enrichment. The exact endpoint surface varies, so this implementation keeps NLK isolated and optional. If it is unavailable, metadata gracefully falls back to Naver.

### Fallback mode
If API keys are missing or a provider fails, the package still works by:

- searching through Naver if available
- otherwise returning clear provider errors
- using deterministic recommendation ranking from related search results
- using curated Korean trending placeholders when popular-loan APIs are unavailable

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run CLI

```bash
cp .env.example .env
kbook search "데미안"
kbook recommend --query "자기계발"
kbook describe --isbn 9788937460441
kbook trending
```

## Run MCP server

stdio mode:

```bash
kbook-mcp
```

HTTP mode:

```bash
kbook-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

## Example LLM tool usage

Example tool intents:

- `search_book(query="한국 스타트업")`
- `recommend_book(query="불안에 관한 책")`
- `recommend_book(isbn13="9788937460441")`
- `describe_book(isbn13="9788937460441")`
- `trending_books()`

## Example OpenClaw usage

```text
Use the MCP tool `recommend_book` with query="한국 소설 중 몰입감 높은 책".
Return the top 5 recommendations with title, author, publisher, isbn13, and recommendation_reason.
```

## Limitations

- Recommendation quality is strongest when Data4Library is available.
- NLK integration is intentionally conservative because endpoint details can vary by account/product surface.
- Fallback recommendations are lexical and metadata-driven, not embedding-based.
- Summary text depends on upstream metadata quality.

## Roadmap

- add Kyobo/Aladin/Yes24-compatible adapters
- add cached popularity aggregation
- add reranking from user preference signals
- add optional semantic embeddings for Korean descriptions
