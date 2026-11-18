locals {
  create_database = var.autonomous_database_mode == "new"
}

resource "oci_kms_vault" "database" {
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-db"
  vault_type     = "DEFAULT"
}

resource "oci_kms_key" "database" {
  compartment_id      = var.compartment_ocid
  display_name        = "${local.name}-db-secrets"
  management_endpoint = oci_kms_vault.database.management_endpoint
  protection_mode     = "SOFTWARE"
  key_shape {
    algorithm = "AES"
    length    = 32
  }
}

# Vault generates the passwords. Terraform only handles references and metadata.
resource "oci_vault_secret" "database" {
  for_each               = local.create_database ? toset(["runtime", "admin"]) : toset(["runtime"])
  compartment_id         = var.compartment_ocid
  vault_id               = oci_kms_vault.database.id
  key_id                 = oci_kms_key.database.id
  secret_name            = "${local.name}-${each.key}"
  enable_auto_generation = true
  secret_generation_context {
    generation_type     = "PASSPHRASE"
    generation_template = "DBAAS_DEFAULT_PASSWORD"
    passphrase_length   = 24
  }
}

moved {
  from = oci_database_autonomous_database.invoices
  to   = oci_database_autonomous_database.invoices[0]
}

resource "oci_database_autonomous_database" "invoices" {
  count                       = local.create_database ? 1 : 0
  compartment_id              = var.compartment_ocid
  db_name                     = upper(var.autonomous_database_name)
  display_name                = var.autonomous_database_name
  db_workload                 = "OLTP"
  db_version                  = "26ai"
  compute_model               = "ECPU"
  compute_count               = 4
  data_storage_size_in_tbs    = 1
  is_auto_scaling_enabled     = false
  license_model               = "LICENSE_INCLUDED"
  secret_id                   = oci_vault_secret.database["admin"].id
  is_mtls_connection_required = false
  is_access_control_enabled   = true
  whitelisted_ips             = concat([oci_core_vcn.extractor.id], [for c in split(",", var.access_cidrs) : trimspace(c)])
  db_tools_details {
    name       = "APEX"
    is_enabled = true
  }
  db_tools_details {
    name       = "ORDS"
    is_enabled = true
  }
  lifecycle {
    precondition {
      condition     = var.access_cidrs != "" && var.existing_autonomous_database_id == ""
      error_message = "A new database requires approved worker and APEX networks and no existing database selection."
    }
  }
}

data "oci_database_autonomous_database" "existing" {
  count                  = local.create_database ? 0 : 1
  autonomous_database_id = var.existing_autonomous_database_id
  lifecycle {
    precondition {
      condition     = startswith(var.existing_autonomous_database_id, "ocid1.autonomousdatabase.")
      error_message = "Select an existing Autonomous Database from the regional list."
    }
    postcondition {
      condition     = self.state == "AVAILABLE" && self.db_workload == "OLTP" && self.db_version == "26ai" && self.is_dedicated != true && self.open_mode == "READ_WRITE" && self.permission_level == "UNRESTRICTED"
      error_message = "Select an available read-write, unrestricted 26ai serverless OLTP database."
    }
    postcondition {
      condition     = self.is_mtls_connection_required == false && !try(length(self.private_endpoint) > 0, false) && self.is_access_control_enabled != true && !try(length(self.whitelisted_ips) > 0, false)
      error_message = "The existing database must already accept walletless TLS from this installation's new VCN and worker."
    }
    postcondition {
      condition     = alltrue([for tool in ["APEX", "ORDS"] : contains([for detail in self.db_tools_details : detail.name if detail.is_enabled], tool)]) && try(startswith(self.connection_urls[0].apex_url, "https://"), false)
      error_message = "APEX and ORDS must already be enabled on the existing database."
    }
  }
}

locals {
  database_id   = local.create_database ? oci_database_autonomous_database.invoices[0].id : data.oci_database_autonomous_database.existing[0].id
  database_urls = local.create_database ? oci_database_autonomous_database.invoices[0].connection_urls : data.oci_database_autonomous_database.existing[0].connection_urls
  profiles      = local.create_database ? oci_database_autonomous_database.invoices[0].connection_strings[0].profiles : data.oci_database_autonomous_database.existing[0].connection_strings[0].profiles
  dsn = one([for p in local.profiles : p.value
  if p.tls_authentication == "SERVER" && p.consumer_group == "LOW" && p.host_format == "FQDN" && p.protocol == "TCPS"])
}
