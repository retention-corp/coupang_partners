#!/usr/bin/env fish

set repo_dir /Users/gyusupsim/Projects/products/coupang_partners

if test -f "$repo_dir/.env.local"
  for line in (cat "$repo_dir/.env.local")
    if string match -qr '^[A-Z0-9_]+=.*$' -- $line
      set parts (string split -m 1 '=' -- $line)
      set -x $parts[1] $parts[2]
    end
  end
end

if not set -q OPENCLAW_SHOPPING_API_TOKEN
  if set -q OPENCLAW_SHOPPING_API_TOKENS
    set -x OPENCLAW_SHOPPING_API_TOKEN (string split -m 1 ',' -- $OPENCLAW_SHOPPING_API_TOKENS)[1]
  end
end

if not set -q OPENCLAW_SHOPPING_API_TOKEN
  echo "OPENCLAW_SHOPPING_API_TOKEN or OPENCLAW_SHOPPING_API_TOKENS is required"
  exit 1
end

if not set -q OPENCLAW_SHOPPING_BASE_URL
  if set -q OPENCLAW_SHOPPING_BACKEND_URL
    set -x OPENCLAW_SHOPPING_BASE_URL $OPENCLAW_SHOPPING_BACKEND_URL
  else if set -q SHOPPING_COPILOT_BASE_URL
    set -x OPENCLAW_SHOPPING_BASE_URL $SHOPPING_COPILOT_BASE_URL
  else
    set -x OPENCLAW_SHOPPING_BASE_URL "https://a.retn.kr"
  end
end

python3 /Users/gyusupsim/.openclaw/skills/shopping-copilot/scripts/openclaw-shopping-skill.py recommend \
  --backend $OPENCLAW_SHOPPING_BASE_URL \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000
