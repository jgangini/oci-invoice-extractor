output "autonomous_database_id" { value = local.database_id }
output "vault_id" { value = local.vault_id }
output "vault_key_id" { value = local.vault_key_id }
output "runtime_secret_ocid" { value = oci_vault_secret.database["runtime"].id }
output "admin_secret_ocid" { value = local.create_database ? oci_vault_secret.database["admin"].id : "" }
output "db_dsn" {
  value = local.dsn
  precondition {
    condition     = local.dsn != null
    error_message = "The selected Autonomous Database must advertise a walletless TLS LOW service."
  }
}
output "apex_base_url" { value = local.database_urls[0].apex_url }
output "bucket_name" { value = local.source_bucket.name }
output "bucket_compartment_id" { value = local.source_bucket.compartment_id }
output "object_namespace" { value = var.objectstorage_namespace }
output "source_prefix" { value = var.source_prefix }
output "event_rule_id" { value = oci_events_rule.ingest.id }
output "function_id" { value = oci_functions_function.extractor.id }
output "function_invoke_url" { value = "${oci_functions_function.extractor.invoke_endpoint}/20181201/functions/${oci_functions_function.extractor.id}/actions/invoke" }

output "deploy_studio_resource_labels" {
  value = {
    "oci_core_vcn.extractor[0]"                    = "Function virtual network"
    "oci_core_service_gateway.oracle[0]"           = "Oracle Services Gateway"
    "oci_core_route_table.private[0]"              = "Function route table"
    "oci_core_security_list.private[0]"            = "Function security list"
    "oci_core_subnet.functions[0]"                 = "Function subnet"
    "oci_objectstorage_bucket.invoices[0]"         = "Invoice upload bucket"
    "oci_objectstorage_object.source_folder[0]"    = "ZIP upload folder"
    "oci_kms_vault.database[0]"                    = "Database Vault"
    "terraform_data.vault_endpoint_ready[0]"       = "Vault management DNS readiness"
    "oci_kms_key.database[0]"                      = "Database encryption key"
    "terraform_data.secret_namespace[0]"           = "Deployment secret namespace"
    "oci_vault_secret.database[\"runtime\"]"       = "Function database secret"
    "oci_vault_secret.database[\"admin\"]"         = "Database administrator secret"
    "oci_database_autonomous_database.invoices[0]" = "Autonomous AI Database"
    "oci_functions_application.invoices[0]"        = "Invoice Functions application"
    "oci_functions_function.extractor"             = "Invoice extraction function"
    "oci_identity_policy.delivery"                 = "Function invocation policy"
    "oci_identity_policy.source_objects"           = "Invoice object access policy"
    "oci_identity_policy.runtime_secret"           = "Function secret access policy"
    "oci_events_rule.ingest"                       = "ZIP ingestion rule"
    "oci_logging_log_group.invoices[0]"            = "Invoice log group"
    "oci_logging_log.function[0]"                  = "Function invocation log"
  }
}
