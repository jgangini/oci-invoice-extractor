"""Validate release inputs from the pinned Deploy Studio source before provisioning."""

import base64
import ipaddress
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SQL_FILES = ("schema", "api", "uploads", "grants", "seed")


class InputError(ValueError):
    """An actionable message safe to include in the Studio event log."""


def context_file(variable):
    return json.loads(Path(os.environ[variable]).read_text(encoding="utf-8"))


def config_for(region):
    import oci

    config = oci.config.from_file(os.environ["DEPLOY_STUDIO_OCI_CONFIG"])
    config.update(region=region, key_file=os.environ["DEPLOY_STUDIO_OCI_KEY"])
    return config


def read_password(client, secret_id):
    bundle = client.get_secret_bundle(secret_id, stage="CURRENT").data
    return base64.b64decode(bundle.secret_bundle_content.content, validate=True).decode("utf-8")


def password_valid(password, username):
    return (12 <= len(password) <= 30 and password.isascii() and password.isprintable()
            and '"' not in password and username.casefold() not in password.casefold()
            and all(re.search(pattern, password) for pattern in ("[a-z]", "[A-Z]", "[0-9]")))


def check_cidrs(value):
    items = [item.strip() for item in value.split(",")]
    if any(not re.fullmatch(r"[0-9.]+/(?:2[4-9]|3[0-2])", item) for item in items):
        raise InputError("Enter public IPv4 networks with /24 to /32. Add /32 for a single IP address.")
    try:
        networks = [ipaddress.IPv4Network(item) for item in items]
    except ValueError:
        raise InputError("Enter public IPv4 ranges, for example an address followed by /32.") from None
    if any(not net.is_global for net in networks):
        raise InputError("Allowed IP ranges must be public IPv4 networks from /24 to /32.")


def check_bucket(client, context):
    inputs = context["inputs"]
    namespace = client.get_namespace().data
    existing = inputs["bucket_mode"] == "existing"
    name = inputs["existing_bucket_name"] if existing else inputs["new_bucket_name"]
    import oci
    try:
        bucket = client.get_bucket(namespace, name).data
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404 and not existing:
            return
        raise InputError("The selected bucket could not be read with these credentials.") from None
    if not existing:
        raise InputError("This bucket name is already in use. Choose another name or select Use existing bucket.")
    if context["compartment_mode"] == "existing" and bucket.compartment_id != context["compartment"]:
        raise InputError("Select a bucket in the chosen compartment.")
    if (bucket.public_access_type != "NoPublicAccess" or bucket.storage_tier != "Standard"
            or bucket.is_read_only or not bucket.object_events_enabled):
        raise InputError("Select a writable, private Standard bucket with object events enabled.")


def check_inputs(context):
    import oci

    inputs = context["inputs"]
    if inputs["autonomous_database_mode"] == "new":
        check_cidrs(inputs["access_cidrs"])
        secret_inputs = context_file("DEPLOY_STUDIO_SECRETS")["inputs"]
        if not password_valid(secret_inputs["autonomous_database_admin_password"], "ADMIN"):
            raise InputError("Use 12 to 30 characters with uppercase, lowercase and a digit for the database admin password.")
    for path in [ROOT / "database" / f"{name}.sql" for name in SQL_FILES] + [ROOT / "apex/f100.sql", ROOT / "source/invoice-catalog.json"]:
        if not path.is_file() or not path.stat().st_size:
            raise InputError(f"Missing installation file: {path.name}.")
    cfg = config_for(context["region"])
    check_bucket(oci.object_storage.ObjectStorageClient(cfg), context)
    if inputs["autonomous_database_mode"] == "existing":
        import oracledb
        from deploy.hooks.database_target import check_existing_principal, check_install_target, existing_dsn

        database = oci.database.DatabaseClient(cfg).get_autonomous_database(inputs["existing_autonomous_database_id"]).data
        scope = context["compartment"] if context["compartment_mode"] == "existing" else None
        dsn = existing_dsn(database, scope)
        secret_inputs = context_file("DEPLOY_STUDIO_SECRETS")["inputs"]
        password = secret_inputs["autonomous_database_admin_password"]
        with oracledb.connect(user="ADMIN", password=password, dsn=dsn, ssl_server_dn_match=True,
                              tcp_connect_timeout=10) as admin:
            check_existing_principal(admin)
            check_install_target(admin, context["deployment_id"])


def main():
    path = Path(os.environ["DEPLOY_STUDIO_RESULT"])
    from deploy.hooks.database_target import DatabaseTargetError
    try:
        check_inputs(context_file("DEPLOY_STUDIO_CONTEXT"))
        status, message = "passed", "Bucket, database settings and release files verified."
    except Exception as exc:
        # Keep OCI error bodies and Vault values outside the deployment history.
        status = "failed"
        message = str(exc) if isinstance(exc, (InputError, DatabaseTargetError)) else "Check OCI access, database credentials and connectivity from the installer."
    path.write_text(json.dumps({"events": [{"name": "Invoice Extractor", "status": status, "message": message}]}), encoding="utf-8")
    return int(status == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
