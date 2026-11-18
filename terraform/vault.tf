variable "vault_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.vault_mode)
    error_message = "Select a new or existing Vault."
  }
}

variable "existing_vault_id" {
  type    = string
  default = ""
}

variable "existing_key_id" {
  type    = string
  default = ""
}

moved {
  from = oci_kms_vault.database
  to   = oci_kms_vault.database[0]
}

moved {
  from = oci_kms_key.database
  to   = oci_kms_key.database[0]
}

locals {
  create_vault  = var.vault_mode == "new"
  vault_id      = local.create_vault ? oci_kms_vault.database[0].id : data.oci_kms_vault.existing[0].id
  vault_key_id  = local.create_vault ? oci_kms_key.database[0].id : data.oci_kms_key.existing[0].id
  secret_prefix = local.create_vault ? local.name : "${local.name}-${terraform_data.secret_namespace[0].id}"
}

# Keep names unique in a shared Vault, even if two deployments have the same short Studio suffix.
resource "terraform_data" "secret_namespace" {
  count = local.create_vault ? 0 : 1
}

resource "oci_kms_vault" "database" {
  count          = local.create_vault ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-db"
  vault_type     = "DEFAULT"
  lifecycle {
    precondition {
      condition     = var.existing_vault_id == "" && var.existing_key_id == ""
      error_message = "Clear existing Vault and key selections when creating a new Vault."
    }
  }
}

resource "oci_kms_key" "database" {
  count               = local.create_vault ? 1 : 0
  compartment_id      = var.compartment_ocid
  display_name        = "${local.name}-db-secrets"
  management_endpoint = oci_kms_vault.database[0].management_endpoint
  protection_mode     = "SOFTWARE"
  key_shape {
    algorithm = "AES"
    length    = 32
  }
}

data "oci_kms_vault" "existing" {
  count    = local.create_vault ? 0 : 1
  vault_id = var.existing_vault_id
  lifecycle {
    precondition {
      condition     = startswith(var.existing_vault_id, "ocid1.vault.")
      error_message = "Select a Vault from the regional list."
    }
    postcondition {
      condition     = self.state == "ACTIVE" && contains(["DEFAULT", "VIRTUAL_PRIVATE"], self.vault_type)
      error_message = "Select an active default or virtual private Vault."
    }
  }
}

data "oci_kms_key" "existing" {
  count               = local.create_vault ? 0 : 1
  key_id              = var.existing_key_id
  management_endpoint = data.oci_kms_vault.existing[0].management_endpoint
  lifecycle {
    precondition {
      condition     = startswith(var.existing_key_id, "ocid1.key.")
      error_message = "Select a master encryption key from the selected Vault."
    }
    postcondition {
      condition     = self.vault_id == var.existing_vault_id && self.state == "ENABLED" && try(self.key_shape[0].algorithm, "") == "AES" && contains(["HSM", "SOFTWARE"], self.protection_mode)
      error_message = "Select an enabled AES master encryption key in the selected Vault with HSM or software protection."
    }
  }
}
