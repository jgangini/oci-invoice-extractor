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
            ("autonomous_database_mode", "autonomous_database_version", "existing_autonomous_database_id",
             "oci_autonomous_oltp_databases"),
        ]:
            self.assertEqual(fields[mode]["default"], "new")
            self.assertEqual(fields[create]["visible_when"], {"field": mode, "equals": "new"})
            self.assertEqual(fields[existing]["visible_when"], {"field": mode, "equals": "existing"})
            self.assertEqual(fields[existing]["options_source"], source)
        self.assertTrue({"function_image_ocid", "function_image_digest", "admin_secret_ocid",
                         "runtime_secret_ocid"}.isdisjoint(fields))
        self.assertEqual(fields["access_cidrs"]["label"], "Allowed IP ranges")
        self.assertEqual(manifest["form"]["email_access_fields"], ["apex_operator", "apex_operator_password"])
        self.assertTrue(fields["apex_operator_password"]["secret"])
        self.assertEqual(fields["source_prefix"]["visible_when"], {"field": "bucket_mode", "equals": "existing"})
        self.assertEqual(fields["source_prefix"]["default"], "source/")
        admin = fields["autonomous_database_admin_password"]
        self.assertNotIn("visible_when", admin)
        self.assertEqual(admin["generate_password_when"], {"field": "autonomous_database_mode", "equals": "new"})
        self.assertEqual(fields["autonomous_database_version"]["default"], "26ai:OLTP")
        for name, source in [("existing_vault_id", "oci_active_vaults"), ("existing_key_id", "oci_vault_aes_keys")]:
            self.assertEqual(fields[name]["options_source"], source)
            self.assertEqual(fields[name]["visible_when"], {"field": "vault_mode", "equals": "existing"})
        self.assertEqual(manifest["pre_destroy"]["when"], {"field": "autonomous_database_mode", "equals": "existing"})

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

    @staticmethod
    def endpoint_outputs(host, function="ocid1.fnfunc.oc1.ord.test123"):
        return {"function_id": function, "function_invoke_url": f"https://{host}/20181201/functions/{function}/actions/invoke",
                "object_namespace": "testns", "bucket_name": "invoice-test"}

    def test_resource_principal_install_can_resume_after_grant(self):
        for present in (0, 1):
            with self.subTest(schema_grant_present=present):
                host = ("test1.functions.us-chicago-1.oci.oraclecloud.com",
                        "test1.us-chicago-1.functions.oci.oraclecloud.com")[present]
                admin = MagicMock()
                cur = admin.cursor.return_value.__enter__.return_value
                cur.fetchone.side_effect = [(1,), (present,)]
                base = post_apply.enable_retry(admin, "us-chicago-1", self.endpoint_outputs(host + ":443"))
                self.assertEqual(base, "https://objectstorage.us-chicago-1.oraclecloud.com/n/testns/b/invoice-test/o/")
                statements = [call.args[0].lower() for call in cur.execute.call_args_list]
                self.assertEqual(sum("enable_resource_principal(" in sql for sql in statements), 1 - present)
                grants = [call.kwargs["host"] for call in cur.execute.call_args_list if "append_host_ace" in call.args[0]]
                self.assertEqual(grants, [host, "objectstorage.us-chicago-1.oraclecloud.com"])

    def test_endpoints_follow_the_sdk_region_realm(self):
        host = "test1.functions.us-gov-ashburn-1.oci.oraclegovcloud.com"
        outputs = self.endpoint_outputs(host, "ocid1.fnfunc.oc3.us-gov-ashburn-1.test123")
        outputs["bucket_name"] = "invoice fixture"
        hosts, base = post_apply.regional_endpoints("us-gov-ashburn-1", outputs)
        self.assertEqual(hosts, (host, "objectstorage.us-gov-ashburn-1.oraclegovcloud.com"))
        self.assertEqual(base, "https://objectstorage.us-gov-ashburn-1.oraclegovcloud.com/n/testns/b/invoice%20fixture/o/")
        with self.assertRaisesRegex(ValueError, "region"):
            post_apply.regional_endpoints("unregistered-1", outputs)
        with patch("oci.regions.endpoint_for", return_value="https://example.test"), self.assertRaisesRegex(
            ValueError, "Object Storage endpoint"
        ):
            post_apply.regional_endpoints("us-gov-ashburn-1", outputs)

    def test_invalid_invocation_authorities_and_paths_never_reach_database(self):
        host = "test1.functions.us-chicago-1.oci.oraclecloud.com"
        outputs = self.endpoint_outputs(host)
        real = outputs["function_invoke_url"]
        for invalid in (
            real.replace("https:", "http:"), real.replace(host, host + ":1443"),
            real.replace(host, "user@" + host), real.replace(host, host + "@example.test"),
            real.replace(host, host + ".example.test"), real.replace(host, "extra." + host),
            real.replace("us-chicago-1", "us-phoenix-1"), real.replace("oraclecloud.com", "oraclegovcloud.com"),
            real.replace("test123", "other"), real + "?q=1", real + "#fragment", real + "/",
        ):
            with self.subTest(url=invalid):
                admin = MagicMock()
                with self.assertRaisesRegex(ValueError, "invocation URL"):
                    post_apply.enable_retry(admin, "us-chicago-1", {**outputs, "function_invoke_url": invalid})
                admin.cursor.assert_not_called()

    def test_catalog_upload_does_not_replace_an_existing_object(self):
        from oci.exceptions import ServiceError

        storage = Mock()
        outputs = {"object_namespace": "tenant", "bucket_name": "new", "source_prefix": "source/"}
        catalog = '{"name":"Invoice Catalog"}'
        post_apply.put_initial_catalog(storage, outputs, catalog)
        storage.put_object.assert_called_once_with("tenant", "new", "source/invoice-catalog.json",
                                                   catalog.encode(), content_type="application/json", if_none_match="*")
        storage.put_object.side_effect = ServiceError(412, "PreconditionFailed", {}, "exists")
        existing = storage.get_object.return_value.data
        existing.content = catalog.encode()
        post_apply.put_initial_catalog(storage, outputs, catalog)
        existing.content += b"!"
        with self.assertRaisesRegex(ValueError, "different content"):
            post_apply.put_initial_catalog(storage, outputs, catalog)
        self.assertEqual(existing.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
