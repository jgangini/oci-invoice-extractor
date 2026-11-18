from hashlib import sha256
import unittest
from unittest.mock import MagicMock

from deploy.hooks import database_target, post_apply, uninstall


PROFILE = "INV_P_" + "A1" * 16
JOB = "studio-ownership-test"


class InstallationOwnershipTests(unittest.TestCase):
    def test_reserved_ids_are_checked_before_the_ownership_table_is_created(self):
        cur = MagicMock()
        cur.fetchone.return_value = (1,)
        with self.assertRaisesRegex(database_target.DatabaseTargetError, "identifiers already exist"):
            database_target._reserve_claim(cur, sha256(JOB.encode()).hexdigest())
        cur.execute.assert_called_once()
        self.assertIn("dba_profiles", cur.execute.call_args.args[0])
        self.assertIn("apex_workspaces", cur.execute.call_args.args[0])

    def test_recovers_the_account_after_a_lost_create_reply(self):
        for name in database_target.SCHEMAS:
            with self.subTest(name=name):
                first = MagicMock()
                first.fetchone.return_value = (None, None, None)
                first.execute.side_effect = [None, ConnectionError("lost DDL reply")]
                with self.assertRaises(ConnectionError):
                    post_apply.prepare_account(first, name, PROFILE, "Example$Only12345")
                create_sql = first.execute.call_args.args[0]
                self.assertIn("create user " + name, create_sql)
                self.assertIn("profile " + PROFILE, create_sql)
                self.assertNotIn("Example$Only12345", create_sql)

                second = MagicMock()
                second.fetchone.return_value = (None, 48, PROFILE)
                second.rowcount = 1
                post_apply.prepare_account(second, name, PROFILE, "Example$Only12345")
                reads, receipt, grant = second.execute.call_args_list
                self.assertIn("dba_users", reads.args[0])
                self.assertIn("user_id=:user_id and profile=:profile", receipt.args[0])
                self.assertEqual(receipt.kwargs, dict(user_id=48, name=name, profile=PROFILE))
                self.assertEqual(grant.args[0], "grant create session to " + name)

    def test_account_collision_or_replacement_fails_before_modification(self):
        for identity in [(None, 47, "DEFAULT"), (48, 47, PROFILE), (48, None, None)]:
            with self.subTest(identity=identity):
                cur = MagicMock()
                cur.fetchone.return_value = identity
                with self.assertRaises(database_target.DatabaseTargetError):
                    post_apply.prepare_account(cur, "INV_DATA", PROFILE, "unused")
                cur.execute.assert_called_once()

    def test_workspace_id_precedes_creation_and_survives_a_lost_reply(self):
        admin = MagicMock()
        cur = admin.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(0,), (776,)]
        cur.execute.side_effect = [None, None, ConnectionError("lost DDL reply")]
        with self.assertRaises(ConnectionError):
            post_apply.create_workspace(admin, "REVIEWER", "unused")
        self.assertEqual(cur.execute.call_args.kwargs, dict(ws_id=776, ws="INVOICES"))
        self.assertIn("p_workspace_id=>:ws_id", cur.execute.call_args.args[0])
        cur.reset_mock(side_effect=True)
        cur.fetchone.side_effect = [(1,), (1,), (19,)]
        post_apply.create_workspace(admin, "REVIEWER", "unused")
        sql = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("w.workspace_id=i.workspace_id", sql[1])
        self.assertFalse(any("add_workspace" in statement for statement in sql))

    def test_uninstall_recovers_after_user_ddl_or_rejects_a_foreign_identity(self):
        for identity, valid in [((None, 48, PROFILE), True), ((None, 48, "DEFAULT"), False),
                                ((47, 48, PROFILE), False)]:
            with self.subTest(identity=identity):
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchall.side_effect = [[("TABLE",)], [(sha256(JOB.encode()).hexdigest(),)]]
                cur.fetchone.side_effect = [
                    (5,), (PROFILE, 12), None, (None, None, None), (None, None, None),
                    identity, (776,), None, identity, (0,),
                ]
                if valid:
                    uninstall.remove_installation(admin, JOB)
                else:
                    with self.assertRaises(database_target.DatabaseTargetError):
                        uninstall.remove_installation(admin, JOB)
                drops = [call.args[0] for call in cur.execute.call_args_list if call.args[0].startswith("drop")]
                self.assertEqual(drops, ["drop user INV_DATA cascade", f"drop profile {PROFILE}",
                                         "drop table admin.inv_extractor_install purge"] if valid else [])

    def test_foreign_profile_assignment_blocks_cleanup_before_ddl(self):
        admin = MagicMock()
        cur = admin.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[("TABLE",)], [(sha256(JOB.encode()).hexdigest(),)]]
        cur.fetchone.side_effect = [(5,), (PROFILE, 12), ("ANOTHER_APP",)]
        with self.assertRaisesRegex(database_target.DatabaseTargetError, "unrelated"):
            uninstall.remove_installation(admin, JOB)
        self.assertTrue(all(call.args[0].startswith("select") for call in cur.execute.call_args_list))

    def test_workspace_recreated_under_same_name_blocks_every_drop(self):
        admin = MagicMock()
        cur = admin.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[("TABLE",)], [(sha256(JOB.encode()).hexdigest(),)]]
        cur.fetchone.side_effect = [(5,), (PROFILE, 12), None, (48, 48, PROFILE),
                                    (49, 49, PROFILE), (50, 50, PROFILE), (776,), (999,)]
        with self.assertRaisesRegex(database_target.DatabaseTargetError, "Workspace ownership"):
            uninstall.remove_installation(admin, JOB)
        self.assertTrue(all(call.args[0].startswith("select") for call in cur.execute.call_args_list))

    def test_legacy_claim_requires_the_original_installer(self):
        admin = MagicMock()
        cur = admin.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[("TABLE",)], [(sha256(JOB.encode()).hexdigest(),)]]
        cur.fetchone.return_value = (0,)
        with self.assertRaisesRegex(database_target.DatabaseTargetError, "Legacy installation record"):
            uninstall.remove_installation(admin, JOB)
        self.assertTrue(all(call.args[0].startswith("select") for call in cur.execute.call_args_list))
