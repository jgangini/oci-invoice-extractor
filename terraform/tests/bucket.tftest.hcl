mock_provider "oci" {}

variables {
  tenancy_ocid            = "tenancy"
  compartment_ocid        = "compartment"
  region                  = "us-chicago-1"
  deployment_suffix       = "test"
  objectstorage_namespace = "example"
  function_image_ocid     = "image"
  function_image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "new_bucket_includes_source_folder" {
  command = plan
  plan_options {
    target = [oci_objectstorage_object.source_folder]
  }
  assert {
    condition     = oci_objectstorage_object.source_folder[0].object == "source/" && oci_objectstorage_object.source_folder[0].bucket == "invoice-uploads"
    error_message = "New uploads must have a visible source/ folder."
  }
}

run "existing_bucket_uses_its_selected_prefix" {
  command = plan
  variables {
    bucket_mode          = "existing"
    existing_bucket_name = "private-uploads"
    source_prefix        = "incoming/"
  }
  plan_options {
    target = [oci_objectstorage_object.source_folder]
  }
  assert {
    condition     = length(oci_objectstorage_object.source_folder) == 0 && length(oci_objectstorage_bucket.invoices) == 0
    error_message = "Reusing a bucket must not add or replace its objects."
  }
}
