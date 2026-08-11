#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")
cd "$PROJECT_ROOT"

mode="${1:-}"

set -- -f deploy/compose.yaml -f deploy/compose.kiwoom.yaml
if [ -s secrets/dart_api_key ]; then
  set -- "$@" -f deploy/compose.dart.yaml
fi
if [ -s secrets/krx_api_key ]; then
  set -- "$@" -f deploy/compose.krx.yaml
fi
naver_news_secret_count=0
for secret_path in secrets/naver_api_hub_client_id secrets/naver_api_hub_client_secret; do
  if [ -s "$secret_path" ]; then
    naver_news_secret_count=$((naver_news_secret_count + 1))
  fi
done
if [ "$naver_news_secret_count" -eq 1 ]; then
  echo "NAVER API HUB News secrets must be present as a complete pair." >&2
  exit 1
fi
if [ "$naver_news_secret_count" -eq 2 ]; then
  set -- "$@" -f deploy/compose.naver-news.yaml
fi

case "$mode" in
  --check)
    exec docker compose "$@" config --quiet
    ;;
  --up)
    exec docker compose "$@" up -d --wait --wait-timeout 180
    ;;
  *)
    echo "Usage: $0 --check|--up" >&2
    exit 2
    ;;
esac
