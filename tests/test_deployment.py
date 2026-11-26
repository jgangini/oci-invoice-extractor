import io
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

from deploy.hooks import post_apply, preflight


class DeploymentTests(unittest.TestCase):
    def test_public_ipv4_requires_an_explicit_cidr_prefix(self):
        for value in ("0.0.0.0/0", "8.8.8.8/32, 1.1.1.0/24", "8.8.8.8/32,1.1.1.1/32"):
            with self.subTest(value=value):
                preflight.check_cidrs(value)
        for value in ("", "8.8.8.8", "1.1.1.0/255.255.255.0", "8.8.8.8/24", "10.0.0.0/24",
                      "1.1.0.0/16", "0.0.0.0/0,1.1.1.1/32", "256.1.1.1/32", "8.8.8.8/32,"):
            with self.subTest(value=value), self.assertRaises(preflight.InputError):
                preflight.check_cidrs(value)

    def test_new_function_network_uses_ipv4_networks_with_a_contained_subnet(self):
        inputs = {"new_vcn_cidr": "10.80.0.0/24", "new_subnet_cidr": "10.80.0.0/25"}
        preflight.check_new_network(inputs)
        for value in ("10.80.0.32/26", "10.81.0.0/25", "10.80.0.0/31", "2001:db8::/24"):
            with self.subTest(value=value), self.assertRaises(preflight.InputError):
                preflight.check_new_network({**inputs, "new_subnet_cidr": value})

    def test_existing_function_network_checks_vcn_compartment_and_private_subnet(self):
        client = Mock()
        inputs = {"existing_network_compartment_id": "ocid1.compartment.oc1..network",
                  "existing_vcn_id": "ocid1.vcn.oc1.iad.network", "existing_subnet_id": "ocid1.subnet.oc1.iad.private"}
        vcn = client.get_vcn.return_value.data
        vcn.id = inputs["existing_vcn_id"]
        vcn.compartment_id = inputs["existing_network_compartment_id"]
        vcn.lifecycle_state = "AVAILABLE"
        subnet = client.get_subnet.return_value.data
        subnet.vcn_id = vcn.id
        subnet.compartment_id = "ocid1.compartment.oc1..other"
        subnet.lifecycle_state = "AVAILABLE"
        subnet.prohibit_public_ip_on_vnic = True
        preflight.check_existing_network(client, inputs, "ocid1.tenancy.oc1..test")
        self.assertEqual(client.mock_calls[:2], [call.get_vcn(inputs["existing_vcn_id"]), call.get_subnet(inputs["existing_subnet_id"])])
        for resource, attribute, value in ((vcn, "compartment_id", "other"), (vcn, "lifecycle_state", "TERMINATED"),
                                           (subnet, "vcn_id", "other"), (subnet, "prohibit_public_ip_on_vnic", False)):
            with self.subTest(attribute=attribute), patch.object(resource, attribute, value), self.assertRaises(preflight.InputError):
                preflight.check_existing_network(client, inputs, "ocid1.tenancy.oc1..test")

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
        self.assertEqual(fields["access_cidrs"]["visible_when"], {"field": "autonomous_database_mode", "equals": "new"})
        self.assertEqual(manifest["form"]["email_access_fields"], ["apex_operator", "apex_operator_password"])
        self.assertEqual(fields["new_bucket_name"]["default"], manifest["project_id"])
        email_fields = [field for service in manifest["email"]["services"] for field in service["fields"]]
        self.assertEqual([field["input"] for field in email_fields if "input" in field],
                         manifest["form"]["email_access_fields"])
        self.assertEqual(
            [(service["title"], [(field["label"], field.get("output", field.get("input")))
                                 for field in service["fields"]]) for service in manifest["email"]["services"]],
            [
                ("Oracle APEX", [("Access URL", "application_url"), ("Username", "apex_operator"),
                                 ("Password", "apex_operator_password")]),
            ],
        )
        self.assertTrue(all(field["output"] in manifest["outputs"] for field in email_fields if "output" in field))
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

    def test_networking_form_defaults_inventory_and_public_ipv4(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / "terraform/deploy-studio.json").read_text())
        fields = {field["name"]: field for field in manifest["form"]["fields"]}
        groups = manifest["form"]["groups"]
        networking = next(group for group in groups if group["id"] == "network")
        self.assertEqual(networking["title"], "Networking")
        self.assertEqual(networking["items"][-1], {"kind": "field", "name": "access_cidrs"})
        self.assertEqual([group["id"] for group in groups if any(
            item["name"] == "access_cidrs" for item in group["items"]
        )], ["network"])
        access = fields["access_cidrs"]
        self.assertEqual(access["default"], "0.0.0.0/0")
        self.assertTrue(access["required"])
        self.assertNotIn("help_text", access)
        mask = re.compile(access["pattern"])
        for value in (access["default"], "8.8.8.8/32", "8.8.8.8/32, 1.1.1.1/32, 1.1.1.0/24"):
            with self.subTest(value=value):
                self.assertIsNotNone(mask.fullmatch(value))
        for value in ("8.8.8.8", "256.1.1.1/32", "8.8.8.8/33", "8.8.8.8/32,", "0.0.0.0/0,1.1.1.1/32"):
            with self.subTest(value=value):
                self.assertIsNone(mask.fullmatch(value))
        self.assertEqual(fields["network_mode"]["default"], "new")
        self.assertEqual(fields["new_vcn_cidr"]["default"], "10.80.0.0/24")
        self.assertEqual(fields["new_subnet_cidr"]["default"], "10.80.0.0/25")
        for name in ("new_vcn_cidr", "new_subnet_cidr"):
            self.assertEqual(fields[name]["visible_when"], {"field": "network_mode", "equals": "new"})
        for name, source, parent in (("existing_network_compartment_id", "oci_network_compartments", None),
                                      ("existing_vcn_id", "oci_active_vcns", "existing_network_compartment_id"),
                                      ("existing_subnet_id", "oci_private_subnets", "existing_vcn_id")):
            self.assertEqual(fields[name]["options_source"], source)
            self.assertEqual(fields[name].get("options_parent"), parent)
            self.assertEqual(fields[name]["visible_when"], {"field": "network_mode", "equals": "existing"})

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

    def test_function_probe_retries_propagation_and_requires_the_read_only_response(self):
        from oci.exceptions import ServiceError

        host = "test1.functions.us-chicago-1.oci.oraclecloud.com"
        outputs = self.endpoint_outputs(host)
        config = {"region": "us-chicago-1"}
        unavailable = ServiceError(404, "NotAuthorizedOrNotFound", {}, "not ready")
        with patch("oci.functions.FunctionsInvokeClient") as factory, patch.object(post_apply.time, "sleep") as sleep:
            invoke = factory.return_value.invoke_function
            for content, passes in ((b'{"status":"superseded"}', True), (b'{"status":"ignored"}', False)):
                with self.subTest(content=content):
                    body = Mock(raw=io.BytesIO(content))
                    invoke.side_effect = [unavailable, SimpleNamespace(status=200, data=body)]
                    if passes:
                        post_apply.verify_function(config, outputs)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "read-only result"):
                            post_apply.verify_function(config, outputs)
                    body.close.assert_called_once()
                    self.assertEqual(invoke.call_args.args, (outputs["function_id"],))
                    self.assertEqual(invoke.call_args.kwargs["fn_invoke_type"], "sync")
                    self.assertEqual(json.loads(invoke.call_args.kwargs["invoke_function_body"]),
                                     {"batch_id": -1, "generation": -1})
            factory.assert_called_with(config, service_endpoint=f"https://{host}", timeout=(5, 40))
            self.assertEqual(sleep.call_args_list, [call(5), call(5)])

    def test_delivery_activates_only_after_the_function_probe(self):
        outputs = {**self.endpoint_outputs("test1.functions.us-chicago-1.oci.oraclecloud.com"),
                   "db_dsn": "fixture", "bucket_compartment_id": "target", "runtime_secret_ocid": "secret",
                   "apex_base_url": "https://db.example/ords/apex"}
        context = {"region": "us-chicago-1", "deployment_id": "fixture", "terraform_outputs": outputs,
                   "inputs": {"apex_operator": "invoice_admin", "autonomous_database_mode": "existing", "bucket_mode": "existing"}}
        secrets = {"inputs": {"apex_operator_password": "synthetic", "autonomous_database_admin_password": "synthetic"}}
        flow = Mock()
        with (patch("oci.events.EventsClient") as events, patch("oci.secrets.SecretsClient"),
              patch("oci.object_storage.ObjectStorageClient"), patch("oracledb.connect"),
              patch("deploy.hooks.admin_password.configure_admin_password"), patch("deploy.hooks.database_target.check_install_target"),
              patch.multiple(post_apply, context_file=Mock(side_effect=[context, secrets] * 2), config_for=Mock(),
                             read_password=Mock(), prepare_users=Mock(), enable_retry=Mock(), schema_session=MagicMock(),
                             install_data=Mock(), create_workspace=Mock(), install_apex=Mock(),
                             delivery=flow.delivery, verify_function=flow.verify_function)):
            flow.verify_function.side_effect = RuntimeError("Function unavailable")
            with self.assertRaisesRegex(RuntimeError, "Function unavailable"):
                post_apply.install([])
            flow.delivery.assert_called_once_with(events.return_value, outputs, "target", False)
            flow.reset_mock(side_effect=True)
            self.assertEqual(post_apply.install([]), {"application_url": "https://db.example/ords/f?p=100:1"})
            self.assertEqual(flow.mock_calls, [call.delivery(events.return_value, outputs, "target", False),
                                               call.verify_function(post_apply.config_for.return_value, outputs),
                                               call.delivery(events.return_value, outputs, "target", True)])

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
