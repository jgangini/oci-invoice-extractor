data "oci_artifacts_container_image" "release" {
  image_id = var.function_image_ocid
}

data "oci_identity_region_subscriptions" "tenancy" {
  tenancy_id = var.tenancy_ocid
}

provider "oci" {
  alias  = "identity"
  region = one([for subscription in data.oci_identity_region_subscriptions.tenancy.region_subscriptions : subscription.region_name if subscription.is_home_region])
}

resource "oci_objectstorage_bucket" "invoices" {
  count                 = var.bucket_mode == "new" ? 1 : 0
  compartment_id        = var.compartment_ocid
  namespace             = var.objectstorage_namespace
  name                  = var.new_bucket_name
  access_type           = "NoPublicAccess"
  object_events_enabled = true
  versioning            = "Enabled"
}

locals {
  image                       = data.oci_artifacts_container_image.release
  create_function_application = var.function_application_mode == "new"
  function_application_id     = local.create_function_application ? oci_functions_application.invoices[0].id : var.existing_function_application_id
}

moved {
  from = oci_functions_application.invoices
  to   = oci_functions_application.invoices[0]
}

resource "oci_functions_application" "invoices" {
  count          = local.create_function_application ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = local.name
  shape          = "GENERIC_X86"
  subnet_ids     = [local.function_subnet_id]
}

resource "oci_functions_function" "extractor" {
  application_id                   = local.function_application_id
  display_name                     = "extract"
  memory_in_mbs                    = 2048
  timeout_in_seconds               = 300
  detached_mode_timeout_in_seconds = 3600
  image                            = "ocir.${var.region}.oci.oraclecloud.com/${var.objectstorage_namespace}/${local.image.repository_name}:${local.image.version}"
  image_digest                     = var.function_image_digest
  config = {
    DB_DSN           = local.dsn
    DB_SECRET_OCID   = oci_vault_secret.database["runtime"].id
    DB_USER          = "INV_FN"
    OBJECT_NAMESPACE = var.objectstorage_namespace
    BUCKET_NAME      = local.source_bucket.name
    SOURCE_PREFIX    = var.source_prefix
  }
  lifecycle {
    precondition {
      condition = local.create_function_application || (
        var.network_mode == "existing" && can(regex("^ocid1[.]fnapp[.]", var.existing_function_application_id))
      )
      error_message = "Select an existing Functions application and its private subnet."
    }
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
  description    = "Invoke the invoice function."
  statements = [
    "Allow service cloudEvents to use fn-invocation in compartment id ${var.compartment_ocid} where target.function.id='${oci_functions_function.extractor.id}'",
    join(" ", [
      "Allow any-user to use functions-family in compartment id ${var.compartment_ocid} where all {",
      "request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.function.id='${oci_functions_function.extractor.id}',",
      "any {request.permission='FN_FUNCTION_READ', request.permission='FN_INVOCATION'}",
      "}",
    ]),
    "Allow any-user to use fn-invocation in compartment id ${var.compartment_ocid} where all {request.principal.type='autonomousdatabase', request.principal.id='${local.database_id}', target.function.id='${oci_functions_function.extractor.id}'}",
  ]
}

resource "oci_identity_policy" "source_objects" {
  provider       = oci.identity
  compartment_id = local.source_bucket.compartment_id
  name           = "${local.name}-source-objects"
  description    = "Read invoices from Functions and create uploads from APEX under the selected prefix."
  statements = [join(" ", [
    "Allow any-user to manage objects in compartment id ${local.source_bucket.compartment_id} where all {",
    "target.bucket.name='${local.source_bucket.name}', target.object.name='${var.source_prefix}*',",
    "any {",
    "all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', request.permission='OBJECT_READ'},",
    "all {request.principal.type='autonomousdatabase', request.principal.id='${local.database_id}', request.permission='OBJECT_CREATE'}",
    "}}",
  ])]
}

resource "oci_identity_policy" "runtime_secret" {
  provider       = oci.identity
  compartment_id = var.compartment_ocid
  name           = "${local.name}-runtime-secret"
  description    = "Only the invoice function may read its database password."
  statements     = ["Allow any-user to read secret-bundles in compartment id ${var.compartment_ocid} where all {request.principal.type='fnfunc', request.principal.id='${oci_functions_function.extractor.id}', target.secret.id='${oci_vault_secret.database["runtime"].id}'}"]
}

resource "oci_events_rule" "ingest" {
  compartment_id = local.source_bucket.compartment_id
  display_name   = "${local.name}-zip"
  is_enabled     = false
  condition = jsonencode({
    eventType = ["com.oraclecloud.objectstorage.createobject"]
    data = {
      resourceName      = [for ext in ["zip", "ziP", "zIp", "zIP", "Zip", "ZiP", "ZIp", "ZIP"] : "${var.source_prefix}*.${ext}"]
      additionalDetails = { bucketName = [local.source_bucket.name], namespace = [var.objectstorage_namespace] }
    }
  })
  actions {
    actions {
      action_type = "FAAS"
      function_id = oci_functions_function.extractor.id
      is_enabled  = true
    }
  }
  depends_on = [oci_identity_policy.delivery, oci_identity_policy.runtime_secret, oci_identity_policy.source_objects]
  # Post-apply activates delivery after verifying the database and APEX installation.
  lifecycle { ignore_changes = [is_enabled] }
}

moved {
  from = oci_logging_log_group.invoices
  to   = oci_logging_log_group.invoices[0]
}

moved {
  from = oci_logging_log.function
  to   = oci_logging_log.function[0]
}

resource "oci_logging_log_group" "invoices" {
  count          = local.create_function_application ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = local.name
}

resource "oci_logging_log" "function" {
  count              = local.create_function_application ? 1 : 0
  display_name       = "invocations"
  log_group_id       = oci_logging_log_group.invoices[0].id
  log_type           = "SERVICE"
  retention_duration = 30
  configuration {
    source {
      source_type = "OCISERVICE"
      service     = "functions"
      category    = "invoke"
      resource    = local.function_application_id
    }
  }
}
