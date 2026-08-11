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

llm_secret_directory="$PROJECT_ROOT/secrets/llm"
mkdir -p "$llm_secret_directory"
chown "$APP_UID:$APP_GID" "$llm_secret_directory"
chmod 0700 "$llm_secret_directory"
find "$llm_secret_directory" -maxdepth 1 -type f -name 'provider-*.key' -exec \
  chown "$APP_UID:$APP_GID" {} \; -exec chmod 0400 {} \;

for secret_name in postgres_password totp_encryption_key; do
  secret_path="$PROJECT_ROOT/secrets/$secret_name"
  if [ ! -s "$secret_path" ]; then
    echo "Missing or empty secret file: $secret_path" >&2
    exit 1
  fi
  chown "$APP_UID:$APP_GID" "$secret_path"
  chmod 0400 "$secret_path"
done

kiwoom_secret_names="kiwoom_mock_app_key kiwoom_mock_app_secret kiwoom_mock_account_id"
kiwoom_secret_count=0
for secret_name in $kiwoom_secret_names; do
  if [ -e "$PROJECT_ROOT/secrets/$secret_name" ]; then
    kiwoom_secret_count=$((kiwoom_secret_count + 1))
  fi
done

if [ "$kiwoom_secret_count" -ne 0 ] && [ "$kiwoom_secret_count" -ne 3 ]; then
  echo "Kiwoom MOCK secrets must be prepared as a complete set of three files." >&2
  exit 1
fi

if [ "$kiwoom_secret_count" -eq 3 ]; then
  for secret_name in $kiwoom_secret_names; do
    secret_path="$PROJECT_ROOT/secrets/$secret_name"
    if [ ! -s "$secret_path" ]; then
      echo "Missing or empty secret file: $secret_path" >&2
      exit 1
    fi
    chown "$APP_UID:$APP_GID" "$secret_path"
    chmod 0400 "$secret_path"
  done
fi

dart_secret_path="$PROJECT_ROOT/secrets/dart_api_key"
if [ -e "$dart_secret_path" ]; then
  if [ ! -s "$dart_secret_path" ]; then
    echo "Missing or empty secret file: $dart_secret_path" >&2
    exit 1
  fi
  chown "$APP_UID:$APP_GID" "$dart_secret_path"
  chmod 0400 "$dart_secret_path"
fi

krx_secret_path="$PROJECT_ROOT/secrets/krx_api_key"
if [ -e "$krx_secret_path" ]; then
  if [ ! -s "$krx_secret_path" ]; then
    echo "Missing or empty secret file: $krx_secret_path" >&2
    exit 1
  fi
  chown "$APP_UID:$APP_GID" "$krx_secret_path"
  chmod 0400 "$krx_secret_path"
fi

naver_news_secret_names="naver_api_hub_client_id naver_api_hub_client_secret"
naver_news_secret_count=0
for secret_name in $naver_news_secret_names; do
  if [ -e "$PROJECT_ROOT/secrets/$secret_name" ]; then
    naver_news_secret_count=$((naver_news_secret_count + 1))
  fi
done

if [ "$naver_news_secret_count" -ne 0 ] && [ "$naver_news_secret_count" -ne 2 ]; then
  echo "NAVER API HUB News secrets must be prepared as a complete set of two files." >&2
  exit 1
fi

if [ "$naver_news_secret_count" -eq 2 ]; then
  for secret_name in $naver_news_secret_names; do
    secret_path="$PROJECT_ROOT/secrets/$secret_name"
    if [ ! -s "$secret_path" ]; then
      echo "Missing or empty secret file: $secret_path" >&2
      exit 1
    fi
    chown "$APP_UID:$APP_GID" "$secret_path"
    chmod 0400 "$secret_path"
  done
fi

echo "Cresta secret ownership and permissions are ready for UID/GID 10001:10001."
