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
