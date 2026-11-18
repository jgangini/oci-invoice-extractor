data "oci_artifacts_container_image" "release" {
  image_id = var.function_image_ocid
}

data "oci_vault_secret" "runtime_metadata" {
  secret_id = var.runtime_secret_ocid
}

data "oci_identity_region_subscriptions" "tenancy" {
  tenancy_id = var.tenancy_ocid
}

provider "oci" {
  alias  = "identity"
  region = one([for subscription in data.oci_identity_region_subscriptions.tenancy.region_subscriptions : subscription.region_name if subscription.is_home_region])
}

resource "oci_objectstorage_bucket" "invoices" {
  compartment_id        = var.compartment_ocid
  namespace             = var.objectstorage_namespace
  name                  = local.name
  access_type           = "NoPublicAccess"
  object_events_enabled = true
  versioning            = "Enabled"
}

resource "oci_database_autonomous_database" "invoices" {
  compartment_id              = var.compartment_ocid
  db_name                     = "inv${var.deployment_suffix}"
  display_name                = local.name
  db_workload                 = "OLTP"
  db_version                  = "26ai"
  compute_model               = "ECPU"
  compute_count               = 4
  data_storage_size_in_tbs    = 1
  is_auto_scaling_enabled     = false
  license_model               = "LICENSE_INCLUDED"
  secret_id                   = var.admin_secret_ocid
  is_mtls_connection_required = false
  whitelisted_ips             = concat([oci_core_vcn.extractor.id], [for c in split(",", var.access_cidrs) : trimspace(c)])
  db_tools_details {
    name       = "APEX"
    is_enabled = true
  }
  db_tools_details {
    name       = "ORDS"
    is_enabled = true
  }
}

locals {
  dsn = one([for p in oci_database_autonomous_database.invoices.connection_strings[0].profiles : p.value
  if p.tls_authentication == "SERVER" && p.consumer_group == "LOW" && p.host_format == "FQDN" && p.protocol == "TCPS"])
  image = data.oci_artifacts_container_image.release
}

resource "oci_functions_application" "invoices" {
  compartment_id = var.compartment_ocid
  display_name   = local.name
  shape          = "GENERIC_X86"
  subnet_ids     = [oci_core_subnet.functions.id]
}

resource "oci_functions_function" "extractor" {
  application_id                   = oci_functions_application.invoices.id
  display_name                     = "extract"
  memory_in_mbs                    = 2048
  timeout_in_seconds               = 300
  detached_mode_timeout_in_seconds = 3600
  image                            = "ocir.${var.region}.oci.oraclecloud.com/${var.objectstorage_namespace}/${local.image.repository_name}:${local.image.version}"
  image_digest                     = var.function_image_digest
  config = {
    DB_DSN           = local.dsn
    DB_SECRET_OCID   = var.runtime_secret_ocid
    DB_USER          = "INV_FN"
    OBJECT_NAMESPACE = var.objectstorage_namespace
    BUCKET_NAME      = oci_objectstorage_bucket.invoices.name
    SOURCE_PREFIX    = var.source_prefix
  }
  lifecycle {
    precondition {
      condition     = local.image.digest == var.function_image_digest && local.image.state == "AVAILABLE" && try(length(local.image.version) > 0 && local.image.version != "latest", false)
      error_message = "The regional OCIR image must match the release digest and have a version tag."
    }
  }
}

resource "oci_identity_policy" "delivery" {
  provider       = oci.identity
  compartment_id = var.compartment_ocid
  name           = "${local.name}-delivery"
  description    = "Invoke the invoice function and read its source bucket."
  statements = [
    "Allow service cloudEvents to use fn-invocation in compartment id ${var.compartment_ocid} where target.function.id='${oci_functions_function.extractor.id}'",
    "Allow any-user to use fn-invocation in compartment id ${var.compartment_ocid} where all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.function.id='${oci_functions_function.extractor.id}'}",
    "Allow any-user to read fn-function in compartment id ${var.compartment_ocid} where all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.function.id='${oci_functions_function.extractor.id}'}",
    "Allow any-user to use fn-invocation in compartment id ${var.compartment_ocid} where all {request.principal.type='autonomousdatabase', request.principal.id='${oci_database_autonomous_database.invoices.id}', target.function.id='${oci_functions_function.extractor.id}'}",
    "Allow any-user to read objects in compartment id ${var.compartment_ocid} where all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.bucket.name='${oci_objectstorage_bucket.invoices.name}', target.object.name='${var.source_prefix}*'}"
  ]
}

resource "oci_identity_policy" "runtime_secret" {
  provider       = oci.identity
  compartment_id = data.oci_vault_secret.runtime_metadata.compartment_id
  name           = "${local.name}-runtime-secret"
  description    = "Only the invoice function may read its database password."
  statements     = ["Allow any-user to read secret-bundles in compartment id ${data.oci_vault_secret.runtime_metadata.compartment_id} where all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.secret.id='${var.runtime_secret_ocid}'}"]
}

resource "oci_events_rule" "ingest" {
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-zip"
  is_enabled     = false
  condition = jsonencode({
    eventType = ["com.oraclecloud.objectstorage.createobject"]
    data = {
      resourceName      = [for ext in ["zip", "ziP", "zIp", "zIP", "Zip", "ZiP", "ZIp", "ZIP"] : "${var.source_prefix}*.${ext}"]
      additionalDetails = { bucketName = [oci_objectstorage_bucket.invoices.name], namespace = [var.objectstorage_namespace] }
    }
  })
  actions {
    actions {
      action_type = "FAAS"
      function_id = oci_functions_function.extractor.id
      is_enabled  = true
    }
  }
  depends_on = [oci_identity_policy.delivery, oci_identity_policy.runtime_secret]
  # Post-apply activates delivery after verifying the database and APEX installation.
  lifecycle { ignore_changes = [is_enabled] }
}

resource "oci_logging_log_group" "invoices" {
  compartment_id = var.compartment_ocid
  display_name   = local.name
}

resource "oci_logging_log" "function" {
  display_name       = "invocations"
  log_group_id       = oci_logging_log_group.invoices.id
  log_type           = "SERVICE"
  retention_duration = 30
  configuration {
    source {
      source_type = "OCISERVICE"
      service     = "functions"
      category    = "invoke"
      resource    = oci_functions_application.invoices.id
    }
  }
}
