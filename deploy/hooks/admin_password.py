"""Finish the one-time ADMIN password handoff for a newly provisioned database."""

import base64
import hmac

import oci
import oracledb

from deploy.hooks.database_target import DatabaseTargetError


VERSION_NAME = "studio-admin"


def _content(bundle):
    return base64.b64decode(bundle.secret_bundle_content.content, validate=True)


def _pending(secrets, secret_id):
    try:
        return secrets.get_secret_bundle(secret_id, stage="PENDING").data
    except oci.exceptions.ServiceError as exc:
        if exc.status != 404:
            raise
        return None


def _ready(vault, secret_id):
    return oci.wait_until(vault, vault.get_secret(secret_id), "lifecycle_state", "ACTIVE",
                          max_interval_seconds=4, max_wait_seconds=90)


def _connect(dsn, password):
    return oracledb.connect(user="ADMIN", password=password, dsn=dsn, ssl_server_dn_match=True, tcp_connect_timeout=10)


def _accepts(dsn, password):
    try:
        with _connect(dsn, password) as session:
            session.ping()
        return True
    except oracledb.DatabaseError as exc:
        if exc.args[0].code == 1017:
            return False
        raise


def configure_admin_password(config, mode, outputs, password):
    """Keep version one recoverable until the selected password works over TLS."""
    if mode == "existing":
        return
    if mode != "new" or not outputs.get("admin_secret_ocid"):
        raise DatabaseTargetError("Only a newly provisioned database can initialize an ADMIN password.")
    cfg = {**config, "log_requests": False}
    vault = oci.vault.VaultsClient(cfg)
    secrets = oci.secrets.SecretsClient(cfg)
    secret_id = outputs["admin_secret_ocid"]
    dsn = outputs["db_dsn"]
    value = password.encode("utf-8")
    meta = _ready(vault, secret_id)
    current = secrets.get_secret_bundle(secret_id, stage="CURRENT").data
    if current.version_number != meta.data.current_version_number:
        raise DatabaseTargetError("The ADMIN secret changed during installation. Retry the operation.")
    if _pending(secrets, secret_id) is not None:
        raise DatabaseTargetError("An ADMIN credential rotation is already pending.")
    if current.version_number == 1:
        if hmac.compare_digest(_content(current), value) and _accepts(dsn, password):
            return
        content = oci.vault.models.Base64SecretContentDetails(
            name=VERSION_NAME, stage="CURRENT", content=base64.b64encode(value).decode("ascii"))
        vault.update_secret(secret_id, oci.vault.models.UpdateSecretDetails(
            enable_auto_generation=False, secret_content=content), if_match=meta.headers["etag"])
        # Wait for the write itself. ACTIVE alone can still describe the old version.
        oci.wait_until(vault, vault.get_secret(secret_id), "current_version_number", 2,
                       max_interval_seconds=4, max_wait_seconds=90)
        current = secrets.get_secret_bundle(secret_id, stage="CURRENT").data
    if (current.version_number != 2 or current.version_name != VERSION_NAME
            or not hmac.compare_digest(_content(current), value)):
        raise DatabaseTargetError("The ADMIN credential has already changed outside this installation.")
    _apply_database_password(secrets, secret_id, dsn, password)


def _apply_database_password(secrets, secret_id, dsn, password):
    if _accepts(dsn, password):
        return
    # On recovery either the new login works, or the original login can finish the handoff.
    original = secrets.get_secret_bundle(secret_id, version_number=1).data
    if original.version_number != 1:
        raise DatabaseTargetError("The bootstrap ADMIN credential could not be verified.")
    old = _content(original).decode("utf-8")
    with _connect(dsn, old) as admin:
        admin.changepassword(old, password)
    if not _accepts(dsn, password):
        raise DatabaseTargetError("The new ADMIN login could not be verified.")
