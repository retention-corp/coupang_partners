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

if not set -q COUPANG_ACCESS_KEY
  echo "COUPANG_ACCESS_KEY is required"
  exit 1
end

if not set -q COUPANG_SECRET_KEY
  echo "COUPANG_SECRET_KEY is required"
  exit 1
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

set -q OPENCLAW_SHOPPING_PORT; or set -x OPENCLAW_SHOPPING_PORT "9883"
set -q OPENCLAW_SHOPPING_DB_PATH; or set -x OPENCLAW_SHOPPING_DB_PATH "$repo_dir/.data/openclaw-shopping.sqlite3"
set -q OPENCLAW_SHOPPING_SHORTENER; or set -x OPENCLAW_SHOPPING_SHORTENER "builtin"
set -q OPENCLAW_SHOPPING_PUBLIC_BASE_URL; or set -x OPENCLAW_SHOPPING_PUBLIC_BASE_URL "http://127.0.0.1:$OPENCLAW_SHOPPING_PORT"
set -q OPENCLAW_SHOPPING_ALLOWED_DEEPLINK_HOSTS; or set -x OPENCLAW_SHOPPING_ALLOWED_DEEPLINK_HOSTS "coupang.com,link.coupang.com,www.coupang.com"

python3 "$repo_dir/backend.py"
