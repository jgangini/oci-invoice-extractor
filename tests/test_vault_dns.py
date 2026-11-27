import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "terraform/wait-for-kms-dns.sh"
HOSTNAME = "newvault-management.kms.us-chicago-1.oci.oraclecloud.com"


@unittest.skipUnless(os.name == "posix", "Requires a POSIX shell")
class VaultDnsTests(unittest.TestCase):
    def run_probe(self, ready_after, endpoint=f"https://{HOSTNAME}/", *, lookup_status=2, lookup_elapsed=0, diagnostic=None):
        with tempfile.TemporaryDirectory() as root:
            bin_path = Path(root)
            lookups = bin_path / "lookups"
            sleeps = bin_path / "sleeps"
            limits = bin_path / "limits"
            clock = bin_path / "clock"
            clock.write_text("0\n", encoding="ascii")
            (bin_path / "wc").symlink_to(shutil.which("wc"))
            commands = {
                "getent": ('[ "$1" = ahosts ] && [ "$2" = "$DNS_HOST" ] || exit 2\n'
                           'printf x >> "$DNS_LOOKUPS"\n'
                           '[ "$(wc -c < "$DNS_LOOKUPS")" -ge "$DNS_READY_AFTER" ] || exit "$DNS_LOOKUP_STATUS"\n'),
                "date": 'IFS= read -r now < "$DNS_CLOCK"\nprintf "%s\\n" "$now"\n',
                "sleep": ('printf "%s\\n" "$1" >> "$DNS_SLEEPS"\n'
                          'IFS= read -r now < "$DNS_CLOCK"\n'
                          'printf "%s\\n" "$((now + $1))" > "$DNS_CLOCK"\n'),
                "timeout": ('limit=$1\nshift\n'
                            'if [ "$1" = getent ]; then\n'
                            '  printf "%s\\n" "$limit" >> "$DNS_LIMITS"\n'
                            '  elapsed=$DNS_LOOKUP_ELAPSED\n'
                            '  [ "$elapsed" -le "$limit" ] || elapsed=$limit\n'
                            '  IFS= read -r now < "$DNS_CLOCK"\n'
                            '  printf "%s\\n" "$((now + elapsed))" > "$DNS_CLOCK"\n'
                            'fi\nexec "$@"\n'),
            }
            if diagnostic is not None:
                tool, _, _ = diagnostic
                commands[tool] = 'printf "%s\\n" "$DNS_RESPONSE"\nexit "$DNS_RESPONSE_STATUS"\n'
            for name, command in commands.items():
                stub = bin_path / name
                stub.write_text("#!/bin/sh\n" + command, encoding="utf-8")
                stub.chmod(0o700)

            result = subprocess.run(
                [shutil.which("sh"), str(SCRIPT)], capture_output=True, text=True, timeout=10, env={
                    **os.environ, "PATH": str(bin_path),
                    "KMS_MANAGEMENT_ENDPOINT": endpoint, "DNS_HOST": HOSTNAME,
                    "DNS_LOOKUPS": str(lookups), "DNS_SLEEPS": str(sleeps),
                    "DNS_CLOCK": str(clock), "DNS_LIMITS": str(limits),
                    "DNS_READY_AFTER": str(ready_after),
                    "DNS_LOOKUP_STATUS": str(lookup_status),
                    "DNS_LOOKUP_ELAPSED": str(lookup_elapsed),
                    "DNS_RESPONSE": diagnostic[1] if diagnostic else "",
                    "DNS_RESPONSE_STATUS": str(diagnostic[2]) if diagnostic else "0",
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
        self.assertIn("Vault management DNS is ready.", result.stdout)

    def test_allows_dns_to_become_ready_after_the_previous_cutoff(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lookups, 45)
        self.assertEqual(sleeps, ["10"] * 44)
        self.assertIn("Vault management DNS is ready.", result.stdout)

    def test_fails_at_deadline_when_dns_stays_unavailable(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=91)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 90)
        self.assertEqual(sleeps, ["10"] * 90)
        self.assertIn("DNS did not resolve within 900 seconds", result.stderr)
        self.assertIn("dig and nslookup are unavailable", result.stderr)

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
                result, _, _, _ = self.run_probe(91, diagnostic=("dig", response + "\nPRIVATE_RESOLVER_DETAILS", 0))
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("PRIVATE_RESOLVER_DETAILS", result.stderr)

    def test_reports_system_and_diagnostic_timeouts(self):
        result, _, _, _ = self.run_probe(91, lookup_status=124, diagnostic=("dig", "", 124))
        self.assertIn("system lookup timed out", result.stderr)
        self.assertIn("diagnostic query timed out", result.stderr)

    def test_uses_nslookup_when_dig_is_unavailable(self):
        result, _, _, _ = self.run_probe(91, diagnostic=("nslookup", "server can't find example: NXDOMAIN", 1))
        self.assertIn("NXDOMAIN", result.stderr)

    def test_rejects_an_invalid_endpoint_without_a_query(self):
        result, lookups, sleeps, _ = self.run_probe(ready_after=1, endpoint=f"https://{HOSTNAME}/keys")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 0)
        self.assertEqual(sleeps, [])
