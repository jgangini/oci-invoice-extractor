import base64
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import oci
import oracledb

from deploy.hooks import admin_password as handoff


class PasswordHandoffTests(unittest.TestCase):
    def setUp(self):
        self.original = secrets.token_urlsafe(18)
        self.selected = secrets.token_urlsafe(18)
        self.database_password = self.original
        self.bootstrap = self.bundle(self.original, 1)
        self.current = self.bootstrap
        self.pending = None
        self.etag = 1
        self.outputs = {"admin_secret_ocid": "secret", "db_dsn": "tls-test"}
        self.vault = MagicMock()
        self.vault.update_secret.side_effect = self.update
        reader = MagicMock()
        reader.get_secret_bundle.side_effect = self.read
        self.change_count = 0
        self.session = MagicMock()
        self.session.changepassword.side_effect = self.change
        self.patch(handoff.oci.vault, "VaultsClient", return_value=self.vault)
        self.patch(handoff.oci.secrets, "SecretsClient", return_value=reader)
        self.patch(handoff, "_ready", side_effect=self.metadata)
        self.patch(handoff.oci, "wait_until", side_effect=self.wait_for_current)
        self.connect = self.patch(handoff.oracledb, "connect", side_effect=self.login)

    def patch(self, target, name, **kwargs):
        item = patch.object(target, name, **kwargs)
        self.addCleanup(item.stop)
        return item.start()

    @staticmethod
    def bundle(password, number, name=None):
        return SimpleNamespace(version_number=number, version_name=name, secret_bundle_content=SimpleNamespace(
            content=base64.b64encode(password.encode()).decode("ascii")))

    def metadata(self, _vault, _secret):
        return SimpleNamespace(data=SimpleNamespace(current_version_number=self.current.version_number),
                               headers={"etag": str(self.etag)})

    def wait_for_current(self, vault, response, property_name, expected, **kwargs):
        self.assertEqual((property_name, expected), ("current_version_number", self.current.version_number))

    def read(self, _secret, stage=None, version_number=None):
        if version_number == 1:
            return SimpleNamespace(data=self.bootstrap)
        bundle = self.current if stage == "CURRENT" else self.pending
        if bundle is None:
            raise oci.exceptions.ServiceError(404, "NotFound", {}, "No such version")
        return SimpleNamespace(data=bundle)

    def update(self, _secret, details, if_match):
        self.assertEqual(if_match, str(self.etag))
        content = details.secret_content
        self.assertIsNotNone(content, "The handoff must not require a separate Vault promotion.")
        self.assertFalse(details.enable_auto_generation)
        self.assertEqual(content.stage, "CURRENT")
        self.current = self.bundle(base64.b64decode(content.content).decode(), 2, content.name)
        self.etag += 1

    def login(self, *, user, password, dsn, ssl_server_dn_match, tcp_connect_timeout):
        self.assertEqual((user, dsn, ssl_server_dn_match), ("ADMIN", "tls-test", True))
        if self.database_password != password:
            raise oracledb.DatabaseError(SimpleNamespace(code=1017))
        connection = MagicMock()
        connection.__enter__.return_value = self.session
        return connection

    def change(self, old, new):
        self.assertEqual(old, self.database_password)
        self.database_password = new
        self.change_count += 1

    def configure(self):
        handoff.configure_admin_password({}, "new", self.outputs, self.selected)

    def test_initial_change_and_replay(self):
        self.configure()
        self.configure()
        self.assertEqual(self.database_password, self.selected)
        self.assertEqual(self.current.version_number, 2)
        self.assertEqual(self.change_count, 1)
        self.assertEqual(self.vault.update_secret.call_count, 1)

    def test_crash_after_change_verifies_the_selected_login(self):
        def lost_reply(old, new):
            self.change(old, new)
            raise TimeoutError("reply lost")
        self.session.changepassword.side_effect = lost_reply
        with self.assertRaises(TimeoutError):
            self.configure()
        self.assertEqual((self.current.version_number, self.bootstrap.version_number), (2, 1))
        self.configure()
        self.assertEqual(self.current.version_number, 2)
        self.assertEqual(self.change_count, 1)

    def test_crash_before_change_resumes_using_original(self):
        self.session.changepassword.side_effect = TimeoutError("unavailable")
        with self.assertRaises(TimeoutError):
            self.configure()
        self.assertEqual(self.database_password, self.original)
        self.session.changepassword.side_effect = self.change
        self.configure()
        self.assertEqual(self.current.version_number, 2)
        self.assertEqual(self.change_count, 1)
        self.assertEqual(self.vault.update_secret.call_count, 1)

    def test_denies_foreign_pending_or_independently_rotated_current(self):
        self.pending = self.bundle(self.original, 2, "other-rotation")
        with self.assertRaisesRegex(handoff.DatabaseTargetError, "pending"):
            self.configure()
        self.pending = None
        self.current.version_number = 3
        with self.assertRaisesRegex(handoff.DatabaseTargetError, "already changed"):
            self.configure()
        self.connect.assert_not_called()
        self.vault.update_secret.assert_not_called()

    def test_reuse_never_touches_database_or_vault(self):
        handoff.configure_admin_password({}, "existing", {}, self.selected)
        self.connect.assert_not_called()
        self.vault.update_secret.assert_not_called()
        handoff.oci.secrets.SecretsClient.assert_not_called()
