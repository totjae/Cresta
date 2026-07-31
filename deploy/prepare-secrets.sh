#!/bin/sh
set -eu

APP_UID=10001
APP_GID=10001
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname -- "$SCRIPT_DIR")

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo deploy/prepare-secrets.sh" >&2
  exit 1
fi

for secret_name in postgres_password totp_encryption_key; do
  secret_path="$PROJECT_ROOT/secrets/$secret_name"
  if [ ! -s "$secret_path" ]; then
    echo "Missing or empty secret file: $secret_path" >&2
    exit 1
  fi
  chown "$APP_UID:$APP_GID" "$secret_path"
  chmod 0400 "$secret_path"
done

echo "Cresta secret ownership and permissions are ready for UID/GID 10001:10001."
