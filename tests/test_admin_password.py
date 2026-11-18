import base64
import json
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import oci
import oracledb

from deploy.hooks import admin_password as handoff, post_apply


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
        self.vault.get_secret.side_effect = self.metadata
        self.vault.base_client.request.__name__ = "request"
        self.vault.base_client.request.side_effect = self.metadata
        self.reader = MagicMock()
        self.reader.get_secret_bundle.side_effect = self.read
        self.management_reads = []
        self.bundle_reads = []
        self.change_count = 0
        self.session = MagicMock()
        self.session.changepassword.side_effect = self.change
        self.patch(handoff.oci.vault, "VaultsClient", return_value=self.vault)
        self.patch(handoff.oci.secrets, "SecretsClient", return_value=self.reader)
        self.patch(oci.waiter.time, "sleep", return_value=None)
        self.connect = self.patch(handoff.oracledb, "connect", side_effect=self.login)

    def patch(self, target, name, **kwargs):
        item = patch.object(target, name, **kwargs)
        self.addCleanup(item.stop)
        return item.start()

    @staticmethod
    def bundle(password, number, name=None):
        return SimpleNamespace(version_number=number, version_name=name, secret_bundle_content=SimpleNamespace(
            content=base64.b64encode(password.encode()).decode("ascii")))

    def metadata(self, _request):
        version = self.management_reads.pop(0) if self.management_reads else self.current.version_number
        return SimpleNamespace(data=SimpleNamespace(current_version_number=version, lifecycle_state="ACTIVE"),
                               request=SimpleNamespace(method="GET"), headers={"etag": str(self.etag)})

    def read(self, _secret, stage=None, version_number=None):
        if version_number == 1:
            return SimpleNamespace(data=self.bootstrap)
        bundle = self.current if stage == "CURRENT" else self.pending
        if stage == "CURRENT" and self.bundle_reads:
            bundle = self.bundle_reads.pop(0)
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

    def test_management_and_retrieval_may_converge_at_different_times(self):
        self.management_reads = [1, 1, 2, 2, 2]
        self.bundle_reads = [self.bootstrap, self.bootstrap, None]
        self.configure()
        self.assertEqual(self.vault.base_client.request.call_count, 3)
        self.assertEqual(self.vault.update_secret.call_count, 1)
        self.assertEqual(self.change_count, 1)
        self.assertEqual(self.database_password, self.selected)

    def test_replay_waits_for_both_services_without_republishing(self):
        self.current = self.bundle(self.selected, 2, handoff.VERSION_NAME)
        self.management_reads = [1, 2, 2]
        self.bundle_reads = [self.current, self.bootstrap]
        self.configure()
        self.vault.update_secret.assert_not_called()
        self.assertEqual(self.change_count, 1)

    def test_foreign_bundle_or_rotation_during_wait_cannot_change_the_database(self):
        for version, name, content in [(3, handoff.VERSION_NAME, self.selected),
                                       (2, "unrelated", self.selected),
                                       (2, handoff.VERSION_NAME, self.original)]:
            with self.subTest(version=version, name=name):
                self.current = self.bundle(content, version, name)
                self.management_reads = [2, 2]
                self.bundle_reads = [self.bootstrap, self.current]
                with self.assertRaisesRegex(handoff.AdminHandoffError, "outside this installation"):
                    self.configure()
        self.connect.assert_not_called()
        self.vault.update_secret.assert_not_called()

    def test_rotation_that_appears_after_publication_blocks_database_change(self):
        def concurrent_rotation(secret_id, details, if_match):
            self.update(secret_id, details, if_match)
            self.pending = self.bundle(self.original, 3, "independent-rotation")

        self.vault.update_secret.side_effect = concurrent_rotation
        with self.assertRaisesRegex(handoff.AdminHandoffError, "pending"):
            self.configure()
        self.connect.assert_not_called()
        self.assertEqual(self.change_count, 0)

    def test_wait_failure_reports_which_service_is_lagging_without_credentials(self):
        self.patch(handoff, "WAIT_SECONDS", new=0)
        for version, observed, expected in [
            (1, None, "Vault management"), (2, self.bootstrap, "Secrets retrieval")
        ]:
            with self.subTest(version=version):
                self.current = self.bundle(self.selected, 2, handoff.VERSION_NAME)
                self.management_reads = [version]
                self.bundle_reads = [observed]
                with self.assertRaisesRegex(handoff.AdminHandoffError, expected):
                    handoff._consistent_current(self.vault, self.reader, "secret", minimum=2)
        self.connect.assert_not_called()
        self.vault.update_secret.assert_not_called()

    def test_installation_log_reveals_only_the_fixed_handoff_diagnostic(self):
        writer = self.patch(post_apply.Path, "write_text")
        for problem, expected in [(handoff.AdminHandoffError("bundle_timeout"), "Secrets retrieval"),
                                  (RuntimeError(self.selected), "credential setup.")]:
            def fail(stages):
                stages.append("database admin credential setup")
                raise problem
            with patch.object(post_apply, "install", side_effect=fail), patch.dict(
                post_apply.os.environ, {"DEPLOY_STUDIO_OUTPUT": "status.json"}
            ):
                self.assertEqual(post_apply.main(), 1)
                message = json.loads(writer.call_args.args[0])["events"][0]["message"]
                self.assertIn(expected, message)
                self.assertNotIn(self.selected, message)

    def test_reuse_never_touches_database_or_vault(self):
        handoff.configure_admin_password({}, "existing", {}, self.selected)
        self.connect.assert_not_called()
        self.vault.update_secret.assert_not_called()
        handoff.oci.secrets.SecretsClient.assert_not_called()
