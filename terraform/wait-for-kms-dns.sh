#!/bin/sh
set -eu
export LC_ALL=C

case "${KMS_MANAGEMENT_ENDPOINT:-}" in
    https://*) hostname=${KMS_MANAGEMENT_ENDPOINT#https://}; hostname=${hostname%/} ;;
    *) echo 'Vault management endpoint must be an HTTPS URL.' >&2; exit 1 ;;
esac
hostname=${hostname%.}
case "$hostname" in
    ''|[-.]*|*[!a-zA-Z0-9.-]*) echo 'Invalid Vault management hostname.' >&2; exit 1 ;;
esac

resolver=$(awk '$1 == "nameserver" { print $2; exit }' "${KMS_RESOLV_CONF:-/etc/resolv.conf}")
case "$resolver" in
    ''|*[!0-9a-fA-F:.]*) echo 'The Terraform runner has no usable DNS nameserver.' >&2; exit 1 ;;
esac

if command -v dig >/dev/null; then
    dns_tool=dig
elif command -v nslookup >/dev/null; then
    dns_tool=nslookup
else
    echo 'The Terraform runner requires dig or nslookup to query its DNS nameserver directly.' >&2
    exit 1
fi

if ! command -v timeout >/dev/null; then
    echo 'The Terraform runner requires timeout to bound Vault DNS queries.' >&2
    exit 1
fi

query_dns() {
    if [ "$dns_tool" = dig ]; then
        timeout "$1" dig +time=2 +tries=1 +noall +comments +answer "@$resolver" "$hostname." A
    else
        timeout "$1" nslookup -type=A "$hostname." "$resolver"
    fi
}

has_ipv4_answer() {
    awk -v tool="$dns_tool" '
        /status: NOERROR,/ { noerror = 1 }
        tool == "dig" && $4 == "A" && noerror { ip = $5 }
        tool == "nslookup" && $1 == "Name:" { answer = 1 }
        tool == "nslookup" && answer && $1 == "Address:" { ip = $2 }
        tool == "nslookup" && answer && $1 == "Address" && $2 ~ /^[0-9]+:$/ { ip = $3 }
        ip ~ /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/ { found = 1 }
        END { exit !found }
    '
}

diagnose_dns() {
    case "$answer" in
        *NXDOMAIN*) echo 'Error: Vault DNS probe: NXDOMAIN; the resolver reports that the endpoint hostname does not exist.' ;;
        *SERVFAIL*) echo 'Error: Vault DNS probe: SERVFAIL; the resolver could not complete the query.' ;;
        *REFUSED*) echo 'Error: Vault DNS probe: REFUSED; the DNS server refused the query.' ;;
        *"timed out"*|*"no servers could be reached"*) echo 'Error: Vault DNS probe: no DNS server answered the query.' ;;
        *"status: NOERROR,"*|*"No answer"*) echo 'Error: Vault DNS probe: no IPv4 answer was returned.' ;;
        *)
            if [ "$status" -eq 124 ]; then
                echo 'Error: Vault DNS probe: the DNS query timed out.'
            else
                printf 'Error: Vault DNS probe: no usable IPv4 answer (query exit %s).\n' "$status"
            fi ;;
    esac
}

wait_seconds=900
lookup_seconds=5
poll_seconds=10
deadline=$(($(date +%s) + wait_seconds))
status=124
answer=''

printf 'Waiting up to %s seconds for the Vault management endpoint on the runner DNS nameserver.\n' "$wait_seconds"
while remaining=$((deadline - $(date +%s))); [ "$remaining" -gt 0 ]; do
    lookup_limit=$lookup_seconds
    [ "$remaining" -ge "$lookup_limit" ] || lookup_limit=$remaining
    status=0
    answer=$(query_dns "$lookup_limit" 2>&1) || status=$?
    if [ "$status" -eq 0 ] && printf '%s\n' "$answer" | has_ipv4_answer; then
        printf 'Vault management DNS is ready via %s.\n' "$resolver"
        exit 0
    fi
    remaining=$((deadline - $(date +%s)))
    [ "$remaining" -gt 0 ] || break
    pause=$poll_seconds
    [ "$remaining" -ge "$pause" ] || pause=$remaining
    sleep "$pause"
done

diagnose_dns >&2
printf 'Error: Vault management DNS did not resolve within %s seconds. The key was not created.\n' "$wait_seconds" >&2
exit 1
