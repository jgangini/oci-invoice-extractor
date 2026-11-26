#!/bin/sh
set -eu

case "${KMS_MANAGEMENT_ENDPOINT:-}" in
    https://*) hostname=${KMS_MANAGEMENT_ENDPOINT#https://}; hostname=${hostname%/} ;;
    *) echo 'Vault management endpoint must be an HTTPS URL.' >&2; exit 1 ;;
esac
case "$hostname" in
    ''|[-.]*|*[!a-zA-Z0-9.-]*) echo 'Invalid Vault management hostname.' >&2; exit 1 ;;
esac

if ! command -v getent >/dev/null || ! command -v timeout >/dev/null; then
    echo 'The Terraform runner requires getent and timeout to check Vault DNS.' >&2
    exit 1
fi

echo 'Waiting for the Vault management endpoint to resolve.'
attempt=1
until timeout 5 getent ahosts "$hostname" >/dev/null 2>&1; do
    if [ "$attempt" -ge 30 ]; then
        echo 'Vault management DNS did not resolve after 30 attempts. The key was not created.' >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 5
done
echo 'Vault management DNS is ready.'
