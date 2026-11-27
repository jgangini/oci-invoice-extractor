import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "terraform/wait-for-kms-dns.sh"
HOSTNAME = "newvault-management.kms.us-chicago-1.oci.oraclecloud.com"


@unittest.skipUnless(os.name == "posix" and shutil.which("timeout"), "Requires the Linux Terraform runner tools")
class VaultDnsTests(unittest.TestCase):
    def run_probe(self, ready_after, endpoint=f"https://{HOSTNAME}/", *, lookup_status=2, diagnostic=None):
        with tempfile.TemporaryDirectory() as root:
            bin_path = Path(root)
            lookups = bin_path / "lookups"
            sleeps = bin_path / "sleeps"
            for tool in ("timeout", "wc"):
                (bin_path / tool).symlink_to(shutil.which(tool))
            commands = {
                "getent": ('[ "$1" = ahosts ] && [ "$2" = "$DNS_HOST" ] || exit 2\n'
                           'printf x >> "$DNS_LOOKUPS"\n'
                           '[ "$(wc -c < "$DNS_LOOKUPS")" -ge "$DNS_READY_AFTER" ] || exit "$DNS_LOOKUP_STATUS"\n'),
                "sleep": 'printf "%s\\n" "$1" >> "$DNS_SLEEPS"\n',
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
                    "DNS_READY_AFTER": str(ready_after),
                    "DNS_LOOKUP_STATUS": str(lookup_status),
                    "DNS_RESPONSE": diagnostic[1] if diagnostic else "",
                    "DNS_RESPONSE_STATUS": str(diagnostic[2]) if diagnostic else "0",
                },
            )
            return result, lookups.stat().st_size if lookups.exists() else 0, (
                sleeps.read_text().splitlines() if sleeps.exists() else []
            )

    def test_recovers_after_dns_failures(self):
        result, lookups, sleeps = self.run_probe(ready_after=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lookups, 3)
        self.assertEqual(sleeps, ["5", "5"])
        self.assertIn("Vault management DNS is ready.", result.stdout)

    def test_fails_after_bounded_attempts_when_dns_stays_unavailable(self):
        result, lookups, sleeps = self.run_probe(ready_after=31)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 30)
        self.assertEqual(sleeps, ["5"] * 29)
        self.assertIn("DNS did not resolve after 30 attempts", result.stderr)
        self.assertIn("dig and nslookup are unavailable", result.stderr)

    def test_reports_dns_response_codes_and_no_ipv4_answer(self):
        for response, reason in [
            ("status: NXDOMAIN,", "hostname does not exist"),
            ("status: SERVFAIL,", "could not complete the query"),
            ("status: REFUSED,", "refused the query"),
            ("status: NOERROR, QUERY: 1, ANSWER: 0,", "no IPv4 answer"),
        ]:
            with self.subTest(response=response):
                result, _, _ = self.run_probe(31, diagnostic=("dig", response + "\nPRIVATE_RESOLVER_DETAILS", 0))
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("PRIVATE_RESOLVER_DETAILS", result.stderr)

    def test_reports_system_and_diagnostic_timeouts(self):
        result, _, _ = self.run_probe(31, lookup_status=124, diagnostic=("dig", "", 124))
        self.assertIn("system lookup timed out after 5 seconds", result.stderr)
        self.assertIn("diagnostic query timed out", result.stderr)

    def test_uses_nslookup_when_dig_is_unavailable(self):
        result, _, _ = self.run_probe(31, diagnostic=("nslookup", "server can't find example: NXDOMAIN", 1))
        self.assertIn("NXDOMAIN", result.stderr)

    def test_rejects_an_invalid_endpoint_without_a_query(self):
        result, lookups, sleeps = self.run_probe(ready_after=1, endpoint=f"https://{HOSTNAME}/keys")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 0)
        self.assertEqual(sleeps, [])
