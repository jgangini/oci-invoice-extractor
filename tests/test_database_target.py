from hashlib import sha256
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock

from deploy.hooks.database_target import DatabaseTargetError, check_existing_principal, check_install_target, existing_dsn


def database(**changes):
    profile = NS(tls_authentication="SERVER", consumer_group="LOW", host_format="FQDN", protocol="TCPS", value="tls-low")
    info = dict(compartment_id="selected", lifecycle_state="AVAILABLE", db_workload="OLTP", db_version="26ai",
                is_dedicated=False, open_mode="READ_WRITE", permission_level="UNRESTRICTED",
                is_mtls_connection_required=False, private_endpoint=None, is_access_control_enabled=False,
                whitelisted_ips=[], db_tools_details=[NS(name=name, is_enabled=True) for name in ("APEX", "ORDS")],
                connection_urls=NS(apex_url="https://db.example/ords/apex"), connection_strings=NS(profiles=[profile]))
    return NS(**(info | changes))


class ExistingDatabaseTests(unittest.TestCase):
    def test_external_principal_is_checked_without_changing_database_configuration(self):
        for count in (0, 1):
            admin = MagicMock()
            cur = admin.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (count,)
            if count:
                check_existing_principal(admin)
            else:
                with self.assertRaisesRegex(DatabaseTargetError, "already enabled"):
                    check_existing_principal(admin)
            cur.execute.assert_called_once()
            self.assertTrue(cur.execute.call_args.args[0].startswith("select"))

    def test_reads_walletless_profile_and_validates_compartment_if_requested(self):
        self.assertEqual(existing_dsn(database(), "selected"), "tls-low")
        self.assertEqual(existing_dsn(database()), "tls-low")
        with self.assertRaisesRegex(DatabaseTargetError, "compartment"):
            existing_dsn(database(), "other")

    def test_incompatible_target_is_rejected_without_modifying_it(self):
        for key, value in {"db_workload": "DW", "db_version": "19c", "is_dedicated": True, "open_mode": "READ_ONLY",
                           "permission_level": "RESTRICTED", "lifecycle_state": "STOPPED", "private_endpoint": "private",
                           "whitelisted_ips": ["192.0.2.4/32"], "is_access_control_enabled": True,
                           "is_mtls_connection_required": True, "connection_urls": None, "db_tools_details": [],
                           "connection_strings": None}.items():
            with self.subTest(key=key), self.assertRaises(DatabaseTargetError):
                existing_dsn(database(**{key: value}))

    def test_preflight_and_reservation(self):
        for reserve in (False, True):
            with self.subTest(reserve=reserve):
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [(1,), ("26.1.0",), (0,), None, (0,)]
                cur.fetchall.side_effect = [[], []]
                check_install_target(admin, "studio-1", reserve=reserve)
                ddl = [call.args[0] for call in cur.execute.call_args_list if call.args[0].startswith("create")]
                self.assertEqual(len(ddl), int(reserve))
                if reserve:
                    self.assertIn(sha256(b"studio-1").hexdigest(), ddl[0])

    def test_unowned_names_are_never_adopted(self):
        for count, workspace, apps in [(1, None, []), (0, ("INVOICES",), []), (0, None, [(100, "OTHER", "OTHER", "UNRELATED")])]:
            with self.subTest(schemas=count, workspace=workspace, apps=apps):
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [(1,), ("26.1",), (count,), workspace]
                cur.fetchall.side_effect = [[], apps]
                with self.assertRaisesRegex(DatabaseTargetError, "already in use"):
                    check_install_target(admin, "other", reserve=True)
                self.assertTrue(all(call.args[0].startswith("select") for call in cur.execute.call_args_list))

    def test_retry_accepts_only_own_apex_app_and_workspace(self):
        own = [(100, "INVOICES", "INV_APEX", "INVOICE-EXTRACTOR")]
        for owner, apps, mappings, valid in [
            ("studio-1", own, [("INV_APEX",)], True),
            ("studio-2", own, [("INV_APEX",)], False),
            ("studio-1", [(100, "OTHER", "OTHER", "INVOICE-EXTRACTOR")], [("INV_APEX",)], False),
            ("studio-1", own, [("INV_APEX",), ("ANOTHER",)], False),
        ]:
            with self.subTest(owner=owner, apps=apps, mappings=mappings):
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [(1,), ("26.1",), (5,), (3,), ("INVOICES",)]
                cur.fetchall.side_effect = [[("TABLE",)], [(sha256(owner.encode()).hexdigest(),)], apps, mappings]
                if valid:
                    check_install_target(admin, "studio-1", reserve=True)
                else:
                    with self.assertRaises(DatabaseTargetError):
                        check_install_target(admin, "studio-1", reserve=True)
                self.assertTrue(all(call.args[0].startswith("select") for call in cur.execute.call_args_list))

    def test_requires_instance_visibility_and_supported_apex(self):
        for responses in [[(0,)], [(1,), ("24.2.7",)]]:
            admin = MagicMock()
            admin.cursor.return_value.__enter__.return_value.fetchone.side_effect = responses
            with self.assertRaises(DatabaseTargetError):
                check_install_target(admin, "studio-1")
