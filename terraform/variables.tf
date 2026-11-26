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
variable "autonomous_database_version" {
  type    = string
  default = "26ai"
  validation {
    condition     = var.autonomous_database_version == "26ai"
    error_message = "Choose the supported Oracle AI Database 26ai profile."
  }
}
variable "autonomous_database_workload" {
  type    = string
  default = "OLTP"
  validation {
    condition     = var.autonomous_database_workload == "OLTP"
    error_message = "Choose the OLTP workload for invoice extraction."
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
variable "function_application_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.function_application_mode)
    error_message = "Select a new or an existing Functions application."
  }
}
variable "existing_function_application_id" {
  type    = string
  default = ""
}
variable "network_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.network_mode)
    error_message = "Select a new or an existing virtual cloud network."
  }
}
variable "new_vcn_cidr" {
  type    = string
  default = "10.80.0.0/24"
  validation {
    condition = try(
      can(cidrnetmask(var.new_vcn_cidr)) &&
      can(regex("/(1[6-9]|2[0-9]|30)$", var.new_vcn_cidr)) &&
      cidrsubnet(var.new_vcn_cidr, 0, 0) == var.new_vcn_cidr,
      false
    )
    error_message = "The new VCN must use an IPv4 network CIDR from /16 to /30."
  }
}
variable "new_subnet_cidr" {
  type    = string
  default = "10.80.0.0/25"
  validation {
    condition = try(
      can(cidrnetmask(var.new_subnet_cidr)) &&
      can(regex("/(1[6-9]|2[0-9]|30)$", var.new_subnet_cidr)) &&
      cidrsubnet(var.new_subnet_cidr, 0, 0) == var.new_subnet_cidr,
      false
    )
    error_message = "The new subnet must use an IPv4 network CIDR from /16 to /30."
  }
}
variable "existing_network_compartment_id" {
  type    = string
  default = ""
}
variable "existing_vcn_id" {
  type    = string
  default = ""
}
variable "existing_subnet_id" {
  type    = string
  default = ""
}
variable "access_cidrs" {
  description = "0.0.0.0/0 alone for public access, or comma-separated public IPv4 CIDRs for Deploy Studio and APEX."
  type        = string
  default     = ""
  validation {
    condition = contains(["", "0.0.0.0/0"], trimspace(var.access_cidrs)) || alltrue([
      for c in split(",", var.access_cidrs) : can(cidrnetmask(trimspace(c))) && try(tonumber(split("/", c)[1]) >= 24, false)
    ])
    error_message = "Use 0.0.0.0/0 by itself or supply IPv4 CIDRs /24 to /32 including the worker egress."
  }
}
variable "source_prefix" {
  description = "The allowed source root. APEX routes can narrow it."
  type        = string
  default     = "source/"
  validation {
    condition     = can(regex("^([[:alnum:]_-]+/)+$", var.source_prefix))
    error_message = "Supply a relative folder of letters, digits, underscores or hyphens ending in /."
  }
}
