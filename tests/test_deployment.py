import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from deploy.hooks import post_apply, preflight


class DeploymentTests(unittest.TestCase):
    def test_bucket_reuse_requires_readable_private_event_source(self):
        client = Mock()
        bucket = client.get_bucket.return_value.data
        bucket.compartment_id = "target"
        bucket.public_access_type = "NoPublicAccess"
        bucket.storage_tier = "Standard"
        bucket.is_read_only = False
        bucket.object_events_enabled = True
        context = {"compartment": "target", "compartment_mode": "existing",
                   "inputs": {"bucket_mode": "existing", "existing_bucket_name": "uploads"}}
        preflight.check_bucket(client, context)
        for name, value in [("public_access_type", "ObjectRead"), ("object_events_enabled", False),
                            ("is_read_only", True), ("storage_tier", "Archive"), ("compartment_id", "other")]:
            with self.subTest(name=name), patch.object(bucket, name, value), self.assertRaises(preflight.InputError):
                preflight.check_bucket(client, context)
        client.update_bucket.assert_not_called()
        context["inputs"] = {"bucket_mode": "new", "new_bucket_name": "uploads"}
        with self.assertRaisesRegex(preflight.InputError, "already in use"):
            preflight.check_bucket(client, context)

    def test_deployment_form_supports_creation_and_reuse_by_name(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / "terraform/deploy-studio.json").read_text())
        fields = {field["name"]: field for field in manifest["form"]["fields"]}
        for mode, create, existing, source in [
            ("bucket_mode", "new_bucket_name", "existing_bucket_name", "oci_active_buckets"),
            ("autonomous_database_mode", "autonomous_database_name", "existing_autonomous_database_id",
             "oci_autonomous_oltp_databases"),
        ]:
            self.assertEqual(fields[mode]["default"], "new")
            self.assertEqual(fields[create]["visible_when"], {"field": mode, "equals": "new"})
            self.assertEqual(fields[existing]["visible_when"], {"field": mode, "equals": "existing"})
            self.assertEqual(fields[existing]["options_source"], source)
        self.assertTrue({"function_image_ocid", "function_image_digest", "admin_secret_ocid",
                         "runtime_secret_ocid"}.isdisjoint(fields))
        self.assertEqual(fields["access_cidrs"]["label"], "Allowed IP ranges")

    def test_oracle_statement_delimiters(self):
        script = "create table t (id number);\n/\nbegin\n null;\nend;\n/\n"
        self.assertEqual(list(post_apply.units(script)), ["create table t (id number)", "begin\n null;\nend;"])
        with self.assertRaises(ValueError):
            list(post_apply.units("create table t(id number);"))

    def test_delivery_is_conditional_and_bound_to_stack(self):
        client = Mock()
        outputs = {"event_rule_id": "rule", "function_id": "function"}
        rule = SimpleNamespace(id="rule", compartment_id="scope", is_enabled=False,
                               actions=SimpleNamespace(actions=[SimpleNamespace(function_id="function")]))
        client.get_rule.return_value = SimpleNamespace(headers={"etag": "first"}, data=rule)
        sdk = SimpleNamespace(events=SimpleNamespace(models=SimpleNamespace(UpdateRuleDetails=SimpleNamespace)))
        with patch.dict(sys.modules, {"oci": sdk}):
            with self.assertRaises(ValueError):
                post_apply.delivery(client, outputs, "other", True)
            post_apply.delivery(client, outputs, "scope", False)
            client.update_rule.assert_not_called()
            post_apply.delivery(client, outputs, "scope", True)
            client.update_rule.assert_called_once_with("rule", SimpleNamespace(is_enabled=True), if_match="first")

    def test_hook_returns_phase_and_safe_code_only_on_failure(self):
        with patch.dict(os.environ, {"DEPLOY_STUDIO_OUTPUT": "result.json"}), patch.object(
                post_apply, "install", side_effect=RuntimeError("ORA-01017 sensitive message")), patch.object(
                Path, "write_text") as result:
            self.assertEqual(post_apply.main(), 1)
            data = json.loads(result.call_args.args[0])
        self.assertEqual(data["events"][0]["level"], "error")
        self.assertIn("ORA-01017", data["events"][0]["message"])
        self.assertNotIn("sensitive", data["events"][0]["message"])

    def test_apex_deployment_url(self):
        self.assertEqual(post_apply.application_url("https://db.example/ords/apex"),
                         "https://db.example/ords/f?p=100:1")
        with self.assertRaises(ValueError):
            post_apply.application_url("http://db.example/ords/apex")

    def test_runtime_password_does_not_break_quoted_ddl(self):
        self.assertTrue(preflight.password_valid("Good!1234_test", "INV_FN"))
        self.assertFalse(preflight.password_valid('Bad"1234_test', "INV_FN"))
        self.assertFalse(preflight.password_valid("xInv_Fn1234_safe", "INV_FN"))

    def test_resource_principal_install_can_resume_after_grant(self):
        target = "https://invoke.us-chicago-1.functions.oci.oraclecloud.com"
        for present in (0, 1):
            with self.subTest(schema_grant_present=present):
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [(1,), (present,)]
                post_apply.enable_retry(admin, target)
                statements = [call.args[0].lower() for call in cur.execute.call_args_list]
                self.assertEqual(sum("enable_resource_principal(" in sql for sql in statements), 1 - present)
                self.assertTrue(any("append_host_ace" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
