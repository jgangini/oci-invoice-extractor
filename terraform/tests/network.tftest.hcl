mock_provider "oci" {
  override_during = plan
  mock_data "oci_artifacts_container_image" {
    defaults = {
      digest          = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      state           = "AVAILABLE"
      repository_name = "test/invoice"
      version         = "test"
    }
  }
  mock_data "oci_identity_region_subscriptions" {
    defaults = { region_subscriptions = [{ is_home_region = true, region_name = "us-ashburn-1" }] }
  }
  mock_data "oci_core_services" {
    defaults = { services = [{ id = "osn", cidr_block = "all-iad-services-in-oracle-services-network" }] }
  }
  mock_data "oci_core_vcn" {
    defaults = {
      id             = "ocid1.vcn.oc1.iad.existing"
      compartment_id = "ocid1.compartment.oc1..network"
      state          = "AVAILABLE"
    }
  }
  mock_data "oci_core_subnet" {
    defaults = {
      id                         = "ocid1.subnet.oc1.iad.existing"
      vcn_id                     = "ocid1.vcn.oc1.iad.existing"
      state                      = "AVAILABLE"
      prohibit_public_ip_on_vnic = true
    }
  }
  mock_data "oci_kms_vault" {
    defaults = {
      id                  = "ocid1.vault.oc1.iad.test"
      management_endpoint = "https://example-management.invalid"
      state               = "ACTIVE"
      vault_type          = "DEFAULT"
    }
  }
  mock_data "oci_kms_key" {
    defaults = {
      id              = "ocid1.key.oc1.iad.test"
      vault_id        = "ocid1.vault.oc1.iad.test"
      state           = "ENABLED"
      protection_mode = "HSM"
      key_shape       = [{ algorithm = "AES", length = 32 }]
    }
  }
}

mock_provider "oci" {
  alias = "identity"
}

variables {
  tenancy_ocid            = "ocid1.tenancy.oc1..tenant"
  compartment_ocid        = "ocid1.compartment.oc1..deployment"
  region                  = "us-ashburn-1"
  deployment_suffix       = "test"
  objectstorage_namespace = "example"
  function_image_ocid     = "image"
  function_image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "new_network_uses_private_defaults" {
  command = plan
  plan_options {
    target = [oci_functions_application.invoices]
  }
  override_resource {
    target          = oci_core_subnet.functions[0]
    override_during = plan
    values          = { id = "ocid1.subnet.oc1.iad.new" }
  }
  assert {
    condition = (
      toset(oci_core_vcn.extractor[0].cidr_blocks) == toset(["10.80.0.0/24"]) &&
      oci_core_subnet.functions[0].cidr_block == "10.80.0.0/25" &&
      oci_core_subnet.functions[0].prohibit_public_ip_on_vnic &&
      toset(oci_functions_application.invoices[0].subnet_ids) == toset(["ocid1.subnet.oc1.iad.new"])
    )
    error_message = "New mode must create the default private network and attach Functions to its subnet."
  }
}

run "new_subnet_must_fit_vcn" {
  command = plan
  plan_options {
    target = [oci_core_subnet.functions]
  }
  variables {
    new_subnet_cidr = "10.81.0.0/25"
  }
  expect_failures = [oci_core_subnet.functions]
}

run "reuse_network_without_managing_it" {
  command = plan
  plan_options {
    target = [
      oci_functions_application.invoices, oci_database_autonomous_database.invoices,
      oci_core_vcn.extractor, oci_core_subnet.functions, oci_core_service_gateway.oracle,
      oci_core_route_table.private, oci_core_security_list.private,
    ]
  }
  variables {
    network_mode                    = "existing"
    existing_network_compartment_id = "ocid1.compartment.oc1..network"
    existing_vcn_id                 = "ocid1.vcn.oc1.iad.existing"
    existing_subnet_id              = "ocid1.subnet.oc1.iad.existing"
    access_cidrs                    = "192.0.2.128/32"
  }
  assert {
    condition = (
      length(oci_core_vcn.extractor) + length(oci_core_subnet.functions) +
      length(oci_core_service_gateway.oracle) + length(oci_core_route_table.private) +
      length(oci_core_security_list.private) == 0
    )
    error_message = "Existing mode must leave the VCN, subnet, gateway, routes and security lists unmanaged."
  }
  assert {
    condition = (
      toset(oci_functions_application.invoices[0].subnet_ids) == toset([var.existing_subnet_id]) &&
      oci_database_autonomous_database.invoices[0].whitelisted_ips == toset([var.existing_vcn_id, "192.0.2.128/32"])
    )
    error_message = "Functions must use the selected subnet and the new database ACL must use the selected VCN."
  }
}

run "reject_subnet_from_other_vcn" {
  command = plan
  plan_options {
    target = [data.oci_core_subnet.existing]
  }
  variables {
    network_mode                    = "existing"
    existing_network_compartment_id = "ocid1.compartment.oc1..network"
    existing_vcn_id                 = "ocid1.vcn.oc1.iad.existing"
    existing_subnet_id              = "ocid1.subnet.oc1.iad.existing"
  }
  override_data {
    target          = data.oci_core_subnet.existing[0]
    override_during = plan
    values          = { vcn_id = "ocid1.vcn.oc1.iad.other" }
  }
  expect_failures = [data.oci_core_subnet.existing]
}

run "runtime_policies_scope_actions_to_each_principal" {
  command = plan

  variables {
    bucket_mode          = "existing"
    existing_bucket_name = "private-uploads"
    source_prefix        = "incoming/"
    access_cidrs         = "192.0.2.128/32"
  }

  plan_options {
    target = [oci_events_rule.ingest]
  }

  override_data {
    target          = data.oci_objectstorage_bucket.existing[0]
    override_during = plan
    values = {
      compartment_id        = "ocid1.compartment.oc1..storage"
      access_type           = "NoPublicAccess"
      object_events_enabled = true
      storage_tier          = "Standard"
      is_read_only          = false
    }
  }

  override_resource {
    target          = oci_database_autonomous_database.invoices[0]
    override_during = plan
    values = {
      id                 = "ocid1.autonomousdatabase.oc1.iad.invoices"
      connection_urls    = [{ apex_url = "https://db.example/ords/apex" }]
      connection_strings = [{ profiles = [{ value = "example", tls_authentication = "SERVER", consumer_group = "LOW", host_format = "FQDN", protocol = "TCPS" }] }]
    }
  }

  override_resource {
    target          = oci_functions_function.extractor
    override_during = plan
    values          = { id = "ocid1.fnfunc.oc1.iad.extract" }
  }

  override_resource {
    target          = oci_vault_secret.database["runtime"]
    override_during = plan
    values          = { id = "ocid1.vaultsecret.oc1.iad.runtime" }
  }

  assert {
    condition = oci_identity_policy.delivery.statements == tolist([
      "Allow service cloudEvents to use fn-invocation in compartment id ocid1.compartment.oc1..deployment where target.function.id='ocid1.fnfunc.oc1.iad.extract'",
      "Allow any-user to use functions-family in compartment id ocid1.compartment.oc1..deployment where all { request.principal.type='fnfunc', request.principal.id='ocid1.fnfunc.oc1.iad.extract', target.function.id='ocid1.fnfunc.oc1.iad.extract', any {request.permission='FN_FUNCTION_READ', request.permission='FN_INVOCATION'} }",
      "Allow any-user to use fn-invocation in compartment id ocid1.compartment.oc1..deployment where all {request.principal.type='autonomousdatabase', request.principal.id='ocid1.autonomousdatabase.oc1.iad.invoices', target.function.id='ocid1.fnfunc.oc1.iad.extract'}",
    ])
    error_message = "Events and ADB may only invoke the extractor, and the extractor may only read and invoke itself."
  }

  assert {
    condition = (
      oci_identity_policy.source_objects.compartment_id == "ocid1.compartment.oc1..storage" &&
      oci_identity_policy.source_objects.statements == tolist([
        "Allow any-user to manage objects in compartment id ocid1.compartment.oc1..storage where all { target.bucket.name='private-uploads', target.object.name='incoming/*', any { all {request.principal.type='fnfunc', request.principal.id='ocid1.fnfunc.oc1.iad.extract', request.permission='OBJECT_READ'}, all {request.principal.type='autonomousdatabase', request.principal.id='ocid1.autonomousdatabase.oc1.iad.invoices', request.permission='OBJECT_CREATE'} }}",
      ])
    )
    error_message = "The bucket policy must allow only Function reads and ADB creates under the selected prefix in the actual bucket compartment."
  }

  assert {
    condition = oci_identity_policy.runtime_secret.statements == tolist([
      "Allow any-user to read secret-bundles in compartment id ocid1.compartment.oc1..deployment where all {request.principal.type='fnfunc', request.principal.id='ocid1.fnfunc.oc1.iad.extract', target.secret.id='ocid1.vaultsecret.oc1.iad.runtime'}",
    ])
    error_message = "Only the extractor may read the runtime secret through this policy."
  }
}

# Terraform destroys this provider-mocked state at the end of the test file.
run "reuse_functions_application" {
  command = apply
  variables {
    function_application_mode        = "existing"
    existing_function_application_id = "ocid1.fnapp.oc1.iad.shared"
    network_mode                     = "existing"
    existing_network_compartment_id  = "ocid1.compartment.oc1..network"
    existing_vcn_id                  = "ocid1.vcn.oc1.iad.existing"
    existing_subnet_id               = "ocid1.subnet.oc1.iad.existing"
    vault_mode                       = "existing"
    existing_vault_id                = "ocid1.vault.oc1.iad.test"
    existing_key_id                  = "ocid1.key.oc1.iad.test"
    access_cidrs                     = "192.0.2.128/32"
  }
  override_resource {
    target = oci_database_autonomous_database.invoices[0]
    values = {
      connection_urls    = [{ apex_url = "https://db.example/ords/apex" }]
      connection_strings = [{ profiles = [{ value = "example", tls_authentication = "SERVER", consumer_group = "LOW", host_format = "FQDN", protocol = "TCPS" }] }]
    }
  }
  assert {
    condition = (
      oci_functions_function.extractor.application_id == var.existing_function_application_id &&
      length(oci_functions_application.invoices) == 0 &&
      length(oci_logging_log_group.invoices) == 0 &&
      length(oci_logging_log.function) == 0 &&
      output.function_id == oci_functions_function.extractor.id
    )
    error_message = "The shared application and its logging must stay outside the managed state, while the Function is owned by the deployment."
  }
}
