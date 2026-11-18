mock_provider "oci" {
  mock_data "oci_kms_vault" {
    defaults = {
      id                  = "ocid1.vault.oc1.ord.example"
      management_endpoint = "https://example-management.invalid"
      state               = "ACTIVE"
      vault_type          = "DEFAULT"
    }
  }
  mock_data "oci_kms_key" {
    defaults = {
      id              = "ocid1.key.oc1.ord.example"
      vault_id        = "ocid1.vault.oc1.ord.example"
      state           = "ENABLED"
      protection_mode = "HSM"
      key_shape       = [{ algorithm = "AES", length = 32 }]
    }
  }
}

variables {
  tenancy_ocid            = "tenancy"
  compartment_ocid        = "compartment"
  region                  = "us-chicago-1"
  deployment_suffix       = "test"
  objectstorage_namespace = "example"
  function_image_ocid     = "image"
  function_image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "default_vault_preserves_secret_names" {
  command = plan
  plan_options { target = [oci_vault_secret.database] }
  assert {
    condition     = length(oci_kms_vault.database) == 1 && length(oci_kms_key.database) == 1 && length(data.oci_kms_vault.existing) == 0 && oci_vault_secret.database["admin"].secret_name == "invoice-test-admin"
    error_message = "The default must preserve the original Vault, key and secret names."
  }
}

run "reuse_vault" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
    existing_key_id   = "ocid1.key.oc1.ord.example"
  }
  plan_options { target = [oci_vault_secret.database] }
  override_resource {
    target          = terraform_data.secret_namespace[0]
    override_during = plan
    values          = { id = "b549756c-aacb-4269-8371-99bdaaddeefa" }
  }
  assert {
    condition     = length(oci_kms_vault.database) == 0 && length(oci_kms_key.database) == 0 && output.vault_id == var.existing_vault_id && output.vault_key_id == var.existing_key_id
    error_message = "Reuse must expose the selected Vault and key without managing them."
  }
  assert {
    condition = alltrue([for role, secret in oci_vault_secret.database :
      secret.secret_name == "invoice-test-b549756c-aacb-4269-8371-99bdaaddeefa-${role}" &&
      secret.vault_id == var.existing_vault_id && secret.key_id == var.existing_key_id
    ]) && length(oci_vault_secret.database) == 2
    error_message = "Both generated credentials must belong to this deployment inside the selected Vault."
  }
}

run "missing_vault" {
  command = plan
  variables {
    vault_mode = "existing"
  }
  plan_options { target = [data.oci_kms_vault.existing] }
  expect_failures = [data.oci_kms_vault.existing]
}

run "reuse_both_database_and_vault" {
  command = plan
  variables {
    autonomous_database_mode = "existing"
    vault_mode               = "existing"
    existing_vault_id        = "ocid1.vault.oc1.ord.example"
    existing_key_id          = "ocid1.key.oc1.ord.example"
  }
  plan_options { target = [oci_vault_secret.database] }
  assert {
    condition     = toset(keys(oci_vault_secret.database)) == toset(["runtime"]) && oci_vault_secret.database["runtime"].key_id == var.existing_key_id
    error_message = "A reused database and Vault must receive only this installation's generated runtime secret."
  }
}

run "missing_key" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
  }
  plan_options { target = [data.oci_kms_key.existing] }
  expect_failures = [data.oci_kms_key.existing]
}

run "new_with_existing_selections" {
  command = plan
  variables {
    existing_vault_id = "ocid1.vault.oc1.ord.example"
    existing_key_id   = "ocid1.key.oc1.ord.example"
  }
  plan_options { target = [oci_kms_vault.database] }
  expect_failures = [oci_kms_vault.database]
}

run "inactive_vault" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
  }
  override_data {
    target = data.oci_kms_vault.existing[0]
    values = { state = "PENDING_DELETION", vault_type = "DEFAULT" }
  }
  plan_options { target = [data.oci_kms_vault.existing] }
  expect_failures = [data.oci_kms_vault.existing]
}

run "foreign_key" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
    existing_key_id   = "ocid1.key.oc1.ord.example"
  }
  override_data {
    target = data.oci_kms_key.existing[0]
    values = { vault_id = "ocid1.vault.oc1.ord.other", state = "ENABLED", protection_mode = "HSM", key_shape = [{ algorithm = "AES", length = 32 }] }
  }
  plan_options { target = [data.oci_kms_key.existing] }
  expect_failures = [data.oci_kms_key.existing]
}

run "asymmetric_key" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
    existing_key_id   = "ocid1.key.oc1.ord.example"
  }
  override_data {
    target = data.oci_kms_key.existing[0]
    values = { vault_id = "ocid1.vault.oc1.ord.example", state = "ENABLED", protection_mode = "HSM", key_shape = [{ algorithm = "RSA", length = 256 }] }
  }
  plan_options { target = [data.oci_kms_key.existing] }
  expect_failures = [data.oci_kms_key.existing]
}

run "disabled_key" {
  command = plan
  variables {
    vault_mode        = "existing"
    existing_vault_id = "ocid1.vault.oc1.ord.example"
    existing_key_id   = "ocid1.key.oc1.ord.example"
  }
  override_data {
    target = data.oci_kms_key.existing[0]
    values = { vault_id = "ocid1.vault.oc1.ord.example", state = "DISABLED", protection_mode = "HSM", key_shape = [{ algorithm = "AES", length = 32 }] }
  }
  plan_options { target = [data.oci_kms_key.existing] }
  expect_failures = [data.oci_kms_key.existing]
}
