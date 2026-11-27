import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "terraform/wait-for-kms-dns.sh"
HOSTNAME = "newvault-management.kms.us-chicago-1.oci.oraclecloud.com"
RESOLVER = "169.254.169.254"


@unittest.skipUnless(os.name == "posix", "Requires a POSIX shell")
class VaultDnsTests(unittest.TestCase):
    def run_probe(self, ready_after, endpoint=f"https://{HOSTNAME}/", *, tool="dig", lookup_status=0,
                  lookup_elapsed=0, response="status: NXDOMAIN,"):
        with tempfile.TemporaryDirectory() as root:
            bin_path = Path(root)
            lookups = bin_path / "lookups"
            sleeps = bin_path / "sleeps"
            limits = bin_path / "limits"
            clock = bin_path / "clock"
            resolv = bin_path / "resolv.conf"
            resolv.write_text(f"search example.test\nnameserver {RESOLVER}\n", encoding="ascii")
            clock.write_text("0\n", encoding="ascii")
            for command in ("awk", "wc"):
                (bin_path / command).symlink_to(shutil.which(command))
            commands = {
                "getent": 'printf "192.0.2.9 STREAM %s\\n" "$DNS_HOST"\n',
                "date": 'IFS= read -r now < "$DNS_CLOCK"\nprintf "%s\\n" "$now"\n',
                "sleep": ('printf "%s\\n" "$1" >> "$DNS_SLEEPS"\n'
                          'IFS= read -r now < "$DNS_CLOCK"\n'
                          'printf "%s\\n" "$((now + $1))" > "$DNS_CLOCK"\n'),
                "timeout": ('limit=$1\nshift\n'
                            'printf "%s\\n" "$limit" >> "$DNS_LIMITS"\n'
                            'elapsed=$DNS_LOOKUP_ELAPSED\n'
                            '[ "$elapsed" -le "$limit" ] || elapsed=$limit\n'
                            'IFS= read -r now < "$DNS_CLOCK"\n'
                            'printf "%s\\n" "$((now + elapsed))" > "$DNS_CLOCK"\n'
                            'exec "$@"\n'),
            }
            query = {
                "dig": ('[ "$*" = "+time=2 +tries=1 +noall +comments +answer @$DNS_RESOLVER $DNS_HOST. A" ] || exit 9\n'
                        'ready="status: NOERROR,\n$DNS_HOST. 60 IN A 192.0.2.9"\n'),
                "nslookup": ('[ "$*" = "-type=A $DNS_HOST. $DNS_RESOLVER" ] || exit 9\n'
                             'ready="Name: $DNS_HOST\nAddress: 192.0.2.9"\n'),
            }
            if tool is not None:
                commands[tool] = query[tool] + (
                    'printf x >> "$DNS_LOOKUPS"\n'
                    'if [ "$(wc -c < "$DNS_LOOKUPS")" -ge "$DNS_READY_AFTER" ]; then\n'
                    '  printf "%s\\n" "$ready"\n'
                    'else\n'
                    '  printf "%s\\n" "$DNS_RESPONSE"\n'
                    '  exit "$DNS_LOOKUP_STATUS"\n'
                    'fi\n'
                )
            for name, command in commands.items():
                stub = bin_path / name
                stub.write_text("#!/bin/sh\n" + command, encoding="utf-8")
                stub.chmod(0o700)

            result = subprocess.run(
                [shutil.which("sh"), str(SCRIPT)], capture_output=True, text=True, timeout=10, env={
                    **os.environ, "PATH": str(bin_path),
                    "KMS_MANAGEMENT_ENDPOINT": endpoint, "DNS_HOST": HOSTNAME,
                    "KMS_RESOLV_CONF": str(resolv), "DNS_RESOLVER": RESOLVER,
                    "DNS_LOOKUPS": str(lookups), "DNS_SLEEPS": str(sleeps),
                    "DNS_CLOCK": str(clock), "DNS_LIMITS": str(limits),
                    "DNS_READY_AFTER": str(ready_after),
                    "DNS_LOOKUP_STATUS": str(lookup_status),
                    "DNS_LOOKUP_ELAPSED": str(lookup_elapsed),
                    "DNS_RESPONSE": response,
                },
            )
            return (
                result,
                lookups.stat().st_size if lookups.exists() else 0,
                sleeps.read_text().splitlines() if sleeps.exists() else [],
                limits.read_text().splitlines() if limits.exists() else [],
            )

    def test_recovers_after_dns_failures(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lookups, 3)
        self.assertEqual(sleeps, ["10", "10"])
        self.assertIn(f"Vault management DNS is ready via {RESOLVER}.", result.stdout)

    def test_allows_dns_to_become_ready_after_the_previous_cutoff(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lookups, 45)
        self.assertEqual(sleeps, ["10"] * 44)
        self.assertIn(f"Vault management DNS is ready via {RESOLVER}.", result.stdout)

    def test_fails_at_deadline_when_dns_stays_unavailable(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=91)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 90)
        self.assertEqual(sleeps, ["10"] * 90)
        self.assertIn("DNS did not resolve within 900 seconds", result.stderr)
        self.assertIn("NXDOMAIN", result.stderr)

    def test_requires_a_dns_tool_even_when_nss_resolves_the_host(self):
        result, lookups, _, _ = self.run_probe(1, tool=None)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 0)
        self.assertIn("requires dig or nslookup", result.stderr)

    def test_bounds_slow_lookups_within_deadline(self):
        result, lookups, sleeps, limits = self.run_probe(ready_after=100, lookup_elapsed=4)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 65)
        self.assertEqual(sleeps, ["10"] * 64)
        self.assertEqual(limits, ["5"] * 64 + ["4"])

    def test_reports_dns_response_codes_and_no_ipv4_answer(self):
        for response, reason in [
            ("status: NXDOMAIN,", "hostname does not exist"),
            ("status: SERVFAIL,", "could not complete the query"),
            ("status: REFUSED,", "refused the query"),
            ("status: NOERROR, QUERY: 1, ANSWER: 0,", "no IPv4 answer"),
        ]:
            with self.subTest(response=response):
                result, _, _, _ = self.run_probe(91, response=response + "\nPRIVATE_RESOLVER_DETAILS")
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("PRIVATE_RESOLVER_DETAILS", result.stderr)

    def test_reports_dns_timeout(self):
        result, _, _, _ = self.run_probe(91, lookup_status=124, response="")
        self.assertIn("DNS query timed out", result.stderr)

    def test_uses_nslookup_when_dig_is_unavailable(self):
        result, lookups, _, _ = self.run_probe(2, tool="nslookup", response=f"Server: {RESOLVER}\nAddress: {RESOLVER}#53")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lookups, 2)
        self.assertIn(f"Vault management DNS is ready via {RESOLVER}.", result.stdout)

    def test_rejects_an_invalid_endpoint_without_a_query(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=1, endpoint=f"https://{HOSTNAME}/keys")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 0)
        self.assertEqual(sleeps, [])
