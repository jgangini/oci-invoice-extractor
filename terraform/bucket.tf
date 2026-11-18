moved {
  from = oci_objectstorage_bucket.invoices
  to   = oci_objectstorage_bucket.invoices[0]
}

resource "oci_objectstorage_object" "source_folder" {
  count        = var.bucket_mode == "new" ? 1 : 0
  namespace    = var.objectstorage_namespace
  bucket       = oci_objectstorage_bucket.invoices[0].name
  object       = var.source_prefix
  content      = ""
  content_type = "application/x-directory"
}

variable "bucket_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.bucket_mode)
    error_message = "Select a bucket mode."
  }
}

variable "new_bucket_name" {
  type    = string
  default = "invoice-uploads"
}
variable "existing_bucket_name" {
  type    = string
  default = ""
}

data "oci_objectstorage_bucket" "existing" {
  count     = var.bucket_mode == "existing" ? 1 : 0
  name      = var.existing_bucket_name
  namespace = var.objectstorage_namespace
  lifecycle {
    postcondition {
      condition     = self.access_type == "NoPublicAccess" && self.object_events_enabled && self.storage_tier == "Standard" && !self.is_read_only
      error_message = "Select a writable, private Standard bucket with object events enabled."
    }
  }
}

locals {
  source_bucket = one(concat(oci_objectstorage_bucket.invoices[*], data.oci_objectstorage_bucket.existing[*]))
}
