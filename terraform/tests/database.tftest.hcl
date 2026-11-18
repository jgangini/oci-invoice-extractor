mock_provider "oci" {}

variables {
  tenancy_ocid            = "tenancy"
  compartment_ocid        = "compartment"
  region                  = "us-chicago-1"
  deployment_suffix       = "test"
  objectstorage_namespace = "example"
  function_image_ocid     = "image"
  function_image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "missing_new_database_acl" {
  command = plan
  plan_options {
    target = [oci_database_autonomous_database.invoices]
  }
  expect_failures = [oci_database_autonomous_database.invoices]
}

run "missing_existing_database_selection" {
  command = plan
  variables {
    autonomous_database_mode = "existing"
  }
  plan_options {
    target = [data.oci_database_autonomous_database.existing]
  }
  expect_failures = [data.oci_database_autonomous_database.existing]
}

run "new_database" {
  command = plan
  plan_options {
    target = [oci_database_autonomous_database.invoices, data.oci_database_autonomous_database.existing, oci_vault_secret.database]
  }
  variables {
    autonomous_database_name     = "InvoicesQA"
    autonomous_database_version  = "26ai"
    autonomous_database_workload = "OLTP"
    access_cidrs                 = "192.0.2.128/32"
  }
  assert {
    condition     = oci_database_autonomous_database.invoices[0].db_name == "INVOICESQA" && length(data.oci_database_autonomous_database.existing) == 0
    error_message = "New mode must manage a named database."
  }
  assert {
    condition     = toset(keys(oci_vault_secret.database)) == toset(["runtime", "admin"]) && alltrue([for secret in oci_vault_secret.database : secret.enable_auto_generation])
    error_message = "Vault must generate both passwords in new mode."
  }
  assert {
    condition     = (oci_database_autonomous_database.invoices[0].db_version == "26ai" && oci_database_autonomous_database.invoices[0].db_workload == "OLTP" && oci_database_autonomous_database.invoices[0].compute_model == "ECPU" && oci_database_autonomous_database.invoices[0].compute_count == 4 && oci_database_autonomous_database.invoices[0].data_storage_size_in_tbs == 1)
    error_message = "The provisioned database must match the advertised 26ai OLTP, 4 ECPU, 1 TB profile."
  }
}

run "reuse_database" {
  command = plan
  plan_options {
    target = [data.oci_database_autonomous_database.existing, oci_vault_secret.database, oci_database_autonomous_database.invoices]
  }
  variables {
    autonomous_database_mode        = "existing"
    existing_autonomous_database_id = "ocid1.autonomousdatabase.oc1.test.example"
  }
  override_data {
    target = data.oci_database_autonomous_database.existing[0]
    values = {
      id                          = "ocid1.autonomousdatabase.oc1.test.example"
      state                       = "AVAILABLE"
      db_workload                 = "OLTP"
      db_version                  = "26ai"
      is_dedicated                = false
      open_mode                   = "READ_WRITE"
      permission_level            = "UNRESTRICTED"
      is_mtls_connection_required = false
      private_endpoint            = ""
      is_access_control_enabled   = false
      whitelisted_ips             = []
      db_tools_details            = [{ name = "APEX", is_enabled = true }, { name = "ORDS", is_enabled = true }]
      connection_urls             = [{ apex_url = "https://db.example/ords/apex" }]
      connection_strings          = [{ profiles = [{ value = "example", tls_authentication = "SERVER", consumer_group = "LOW", host_format = "FQDN", protocol = "TCPS" }] }]
    }
  }
  assert {
    condition     = length(oci_database_autonomous_database.invoices) == 0 && local.database_id == var.existing_autonomous_database_id && local.dsn == "example"
    error_message = "Reuse must read the selected database and its actual TLS profile without managing it."
  }
  assert {
    condition     = toset(keys(oci_vault_secret.database)) == toset(["runtime"])
    error_message = "Reuse must generate only the application's password."
  }
}
