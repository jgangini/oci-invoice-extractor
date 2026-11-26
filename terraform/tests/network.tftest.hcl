mock_provider "oci" {
  override_during = plan
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
      toset(oci_functions_application.invoices.subnet_ids) == toset(["ocid1.subnet.oc1.iad.new"])
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
      toset(oci_functions_application.invoices.subnet_ids) == toset([var.existing_subnet_id]) &&
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
