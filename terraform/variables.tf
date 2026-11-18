terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 8.21.0"
    }
  }
}

provider "oci" {
  region = var.region
}

variable "compartment_ocid" { type = string }
variable "tenancy_ocid" { type = string }
variable "region" { type = string }
variable "objectstorage_namespace" { type = string }
variable "deployment_suffix" { type = string }
variable "function_image_ocid" { type = string }
variable "function_image_digest" {
  type = string
  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.function_image_digest))
    error_message = "Supply the immutable OCI manifest digest from release-inputs."
  }
}
variable "autonomous_database_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.autonomous_database_mode)
    error_message = "Select a new or an existing Autonomous Database."
  }
}
variable "autonomous_database_name" {
  type    = string
  default = "INVOICES"
  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9]{0,29}$", var.autonomous_database_name))
    error_message = "Use up to 30 letters and digits, starting with a letter."
  }
}
variable "existing_autonomous_database_id" {
  type    = string
  default = ""
}
variable "access_cidrs" {
  description = "Comma-separated approved IPv4 networks for Deploy Studio egress and APEX clients."
  type        = string
  default     = ""
  validation {
    condition     = var.access_cidrs == "" || alltrue([for c in split(",", var.access_cidrs) : can(cidrnetmask(trimspace(c))) && try(tonumber(split("/", c)[1]) >= 24, false)])
    error_message = "Supply individual IPv4 networks /24 to /32. Include the worker egress."
  }
}
variable "source_prefix" {
  description = "The allowed source root. APEX routes can narrow it."
  type        = string
  default     = "entrada/"
  validation {
    condition     = can(regex("^([[:alnum:]_-]+/)+$", var.source_prefix))
    error_message = "Supply a relative folder of letters, digits, underscores or hyphens ending in /."
  }
}
