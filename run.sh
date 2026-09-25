#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ -z "${ECOMATES_ADMIN_USERNAME:-}" ]; then
  ECOMATES_ADMIN_USERNAME=admin
  export ECOMATES_ADMIN_USERNAME
fi

if [ -z "${ECOMATES_ADMIN_PASSWORD:-}" ]; then
  printf "Admin password: "
  IFS= read -r -s ECOMATES_ADMIN_PASSWORD
  printf "\n"
  if [ -z "$ECOMATES_ADMIN_PASSWORD" ]; then
    printf "An admin password is required.\n" >&2
    exit 1
  fi
  export ECOMATES_ADMIN_PASSWORD
fi

python3 app.py
