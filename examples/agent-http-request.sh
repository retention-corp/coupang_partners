#!/usr/bin/env bash
set -euo pipefail

curl -sS -X POST https://a.retn.kr/v1/public/assist \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: HermesAgent/1.0 (+https://a.retn.kr)' \
  -H 'X-OpenClaw-Client-Id: hermes-agent' \
  -H 'X-OpenClaw-Surface: mcp' \
  -d '{"query":"10만원 이하 로켓배송 무선 마우스","limit":3}'
