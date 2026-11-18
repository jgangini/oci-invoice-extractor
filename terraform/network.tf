locals {
  name = "invoice-${var.deployment_suffix}"
}

data "oci_core_services" "regional" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

locals {
  osn = one(data.oci_core_services.regional.services)
}

resource "oci_core_vcn" "extractor" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.80.0.0/24"]
  display_name   = local.name
  dns_label      = "invvcn"
}

resource "oci_core_service_gateway" "oracle" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor.id
  display_name   = local.name
  services { service_id = local.osn.id }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor.id
  route_rules {
    destination       = local.osn.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.oracle.id
  }
}

resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.extractor.id
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
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.extractor.id
  cidr_block                 = "10.80.0.0/25"
  display_name               = local.name
  dns_label                  = "functions"
  prohibit_public_ip_on_vnic = true
  security_list_ids          = [oci_core_security_list.private.id]
  route_table_id             = oci_core_route_table.private.id
}
