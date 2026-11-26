locals {
  name               = "invoice-${var.deployment_suffix}"
  create_network     = var.network_mode == "new"
  network_vcn_id     = local.create_network ? oci_core_vcn.extractor[0].id : data.oci_core_vcn.existing[0].id
  function_subnet_id = local.create_network ? oci_core_subnet.functions[0].id : data.oci_core_subnet.existing[0].id
  osn                = local.create_network ? one(data.oci_core_services.regional[0].services) : null
}

moved {
  from = oci_core_vcn.extractor
  to   = oci_core_vcn.extractor[0]
}

moved {
  from = oci_core_service_gateway.oracle
  to   = oci_core_service_gateway.oracle[0]
}

moved {
  from = oci_core_route_table.private
  to   = oci_core_route_table.private[0]
}

moved {
  from = oci_core_security_list.private
  to   = oci_core_security_list.private[0]
}

moved {
  from = oci_core_subnet.functions
  to   = oci_core_subnet.functions[0]
}

data "oci_core_services" "regional" {
  count = local.create_network ? 1 : 0
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

data "oci_core_vcn" "existing" {
  count  = local.create_network ? 0 : 1
  vcn_id = var.existing_vcn_id
  lifecycle {
    precondition {
      condition = startswith(var.existing_vcn_id, "ocid1.vcn.") && (
        var.existing_network_compartment_id == var.tenancy_ocid || startswith(var.existing_network_compartment_id, "ocid1.compartment.")
      )
      error_message = "Select an existing VCN and its network compartment."
    }
    postcondition {
      condition     = self.state == "AVAILABLE" && self.compartment_id == var.existing_network_compartment_id
      error_message = "The existing VCN must be available in the selected network compartment."
    }
  }
}

data "oci_core_subnet" "existing" {
  count     = local.create_network ? 0 : 1
  subnet_id = var.existing_subnet_id
  lifecycle {
    precondition {
      condition     = startswith(var.existing_subnet_id, "ocid1.subnet.")
      error_message = "Select an existing private subnet."
    }
    postcondition {
      condition     = self.state == "AVAILABLE" && self.prohibit_public_ip_on_vnic && self.vcn_id == data.oci_core_vcn.existing[0].id
      error_message = "The existing subnet must be private, available, and part of the selected VCN."
    }
  }
}

resource "oci_core_vcn" "extractor" {
  count          = local.create_network ? 1 : 0
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.new_vcn_cidr]
  display_name   = local.name
  dns_label      = "invvcn"
}

resource "oci_core_service_gateway" "oracle" {
  count          = local.create_network ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor[0].id
  display_name   = local.name
  services { service_id = local.osn.id }
}

resource "oci_core_route_table" "private" {
  count          = local.create_network ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor[0].id
  route_rules {
    destination       = local.osn.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.oracle[0].id
  }
}

resource "oci_core_security_list" "private" {
  count          = local.create_network ? 1 : 0
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor[0].id
  display_name   = local.name
  dynamic "egress_security_rules" {
    for_each = toset(["443", "1521", "1522"])
    content {
      destination      = local.osn.cidr_block
      destination_type = "SERVICE_CIDR_BLOCK"
      protocol         = "6"
      tcp_options {
        min = tonumber(egress_security_rules.value)
        max = tonumber(egress_security_rules.value)
      }
    }
  }
}

resource "oci_core_subnet" "functions" {
  count                      = local.create_network ? 1 : 0
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.extractor[0].id
  cidr_block                 = var.new_subnet_cidr
  display_name               = local.name
  dns_label                  = "functions"
  prohibit_public_ip_on_vnic = true
  security_list_ids          = [oci_core_security_list.private[0].id]
  route_table_id             = oci_core_route_table.private[0].id

  lifecycle {
    precondition {
      condition = try(
        tonumber(split("/", var.new_subnet_cidr)[1]) >= tonumber(split("/", var.new_vcn_cidr)[1]) &&
        cidrsubnet("${cidrhost(var.new_subnet_cidr, 0)}/${split("/", var.new_vcn_cidr)[1]}", 0, 0) == var.new_vcn_cidr,
        false
      )
      error_message = "The new subnet CIDR must fit inside the new VCN CIDR."
    }
  }
}
