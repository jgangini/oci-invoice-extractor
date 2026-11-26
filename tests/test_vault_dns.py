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
    def run_probe(self, ready_after, endpoint=f"https://{HOSTNAME}/"):
        with tempfile.TemporaryDirectory() as root:
            bin_path = Path(root)
            lookups = bin_path / "lookups"
            sleeps = bin_path / "sleeps"
            for name, command in {
                "getent": ('[ "$1" = ahosts ] && [ "$2" = "$DNS_HOST" ] || exit 2\n'
                           'printf x >> "$DNS_LOOKUPS"\n'
                           '[ "$(wc -c < "$DNS_LOOKUPS")" -ge "$DNS_READY_AFTER" ]\n'),
                "sleep": 'printf "%s\\n" "$1" >> "$DNS_SLEEPS"\n',
            }.items():
                stub = bin_path / name
                stub.write_text("#!/bin/sh\n" + command, encoding="utf-8")
                stub.chmod(0o700)

            result = subprocess.run(
                ["sh", str(SCRIPT)], capture_output=True, text=True, timeout=10, env={
                    **os.environ, "PATH": f"{bin_path}:{os.environ['PATH']}",
                    "KMS_MANAGEMENT_ENDPOINT": endpoint, "DNS_HOST": HOSTNAME,
                    "DNS_LOOKUPS": str(lookups), "DNS_SLEEPS": str(sleeps),
                    "DNS_READY_AFTER": str(ready_after),
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

    def test_rejects_an_invalid_endpoint_without_a_query(self):
        result, lookups, sleeps = self.run_probe(ready_after=1, endpoint=f"https://{HOSTNAME}/keys")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(lookups, 0)
        self.assertEqual(sleeps, [])
