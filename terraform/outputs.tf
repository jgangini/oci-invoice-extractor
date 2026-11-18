output "autonomous_database_id" { value = local.database_id }
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
