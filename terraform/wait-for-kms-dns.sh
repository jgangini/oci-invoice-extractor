#!/bin/sh
set -eu
export LC_ALL=C

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

diagnose_dns() {
    case "$1" in
        2) echo 'Error: Vault DNS: the system resolver returned no address (getent exit 2).' ;;
        124) echo 'Error: Vault DNS: the system lookup timed out.' ;;
        *) printf 'Error: Vault DNS: the system lookup exited with status %s.\n' "$1" ;;
    esac

    probe_status=0
    if command -v dig >/dev/null; then
        answer=$(timeout 5 dig +time=2 +tries=1 +noall +comments +answer "$hostname" A 2>&1) || probe_status=$?
    elif command -v nslookup >/dev/null; then
        answer=$(timeout 5 nslookup -type=A "$hostname" 2>&1) || probe_status=$?
    else
        echo 'Error: Vault DNS: dig and nslookup are unavailable, so the DNS response code could not be determined.'
        return
    fi
    if [ "$probe_status" -eq 124 ]; then
        echo 'Error: Vault DNS probe: the diagnostic query timed out.'
        return
    fi
    case "$answer" in
        *NXDOMAIN*) echo 'Error: Vault DNS probe: NXDOMAIN; the resolver reports that the endpoint hostname does not exist.' ;;
        *SERVFAIL*) echo 'Error: Vault DNS probe: SERVFAIL; the resolver could not complete the query.' ;;
        *REFUSED*) echo 'Error: Vault DNS probe: REFUSED; the DNS server refused the query.' ;;
        *"timed out"*|*"no servers could be reached"*) echo 'Error: Vault DNS probe: no DNS server answered the query.' ;;
        *"status: NOERROR,"*"ANSWER: 0,"*) echo 'Error: Vault DNS probe: NOERROR but no IPv4 answer was returned.' ;;
        *"status: NOERROR,"*) echo 'Error: Vault DNS probe: NOERROR; the system lookup still returned no usable address.' ;;
        *) echo 'Error: Vault DNS probe: the DNS response code could not be determined.' ;;
    esac
}

wait_seconds=900
lookup_seconds=5
poll_seconds=10
deadline=$(($(date +%s) + wait_seconds))
status=124

printf 'Waiting up to %s seconds for the Vault management endpoint to resolve.\n' "$wait_seconds"
while remaining=$((deadline - $(date +%s))); [ "$remaining" -gt 0 ]; do
    lookup_limit=$lookup_seconds
    [ "$remaining" -ge "$lookup_limit" ] || lookup_limit=$remaining
    if timeout "$lookup_limit" getent ahosts "$hostname" >/dev/null 2>&1; then
        echo 'Vault management DNS is ready.'
        exit 0
    else
        status=$?
    fi
    remaining=$((deadline - $(date +%s)))
    [ "$remaining" -gt 0 ] || break
    pause=$poll_seconds
    [ "$remaining" -ge "$pause" ] || pause=$remaining
    sleep "$pause"
done

diagnose_dns "$status" >&2
printf 'Error: Vault management DNS did not resolve within %s seconds. The key was not created.\n' "$wait_seconds" >&2
exit 1
