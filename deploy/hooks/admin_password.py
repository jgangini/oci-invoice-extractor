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


def _matches_pending(bundle, value):
    if bundle is None or bundle.version_name != VERSION_NAME or not hmac.compare_digest(_content(bundle), value):
        raise DatabaseTargetError("The pending ADMIN secret does not match this deployment. Check the selected password before retrying.")


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
    """Keep the bootstrap and pending versions recoverable until a fresh login succeeds."""
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
    if hmac.compare_digest(_content(current), value):
        return
    if current.version_number != 1 or meta.data.current_version_number != current.version_number:
        raise DatabaseTargetError("The ADMIN credential has already changed. This installer only replaces its bootstrap password.")

    pending = _pending(secrets, secret_id)
    recovering = pending is not None
    if not recovering:
        content = oci.vault.models.Base64SecretContentDetails(
            name=VERSION_NAME, stage="PENDING", content=base64.b64encode(value).decode("ascii"))
        vault.update_secret(secret_id, oci.vault.models.UpdateSecretDetails(
            enable_auto_generation=False, secret_content=content), if_match=meta.headers["etag"])
        meta = _ready(vault, secret_id)
        pending = _pending(secrets, secret_id)
    _matches_pending(pending, value)
    if not (recovering and _accepts(dsn, password)):
        old = _content(current).decode("utf-8")
        with _connect(dsn, old) as admin:
            admin.changepassword(old, password)
        if not _accepts(dsn, password):
            raise DatabaseTargetError("The new ADMIN login could not be verified. Both credential versions remain available for recovery.")
    if meta.data.current_version_number != current.version_number:
        raise DatabaseTargetError("Vault changed during ADMIN password setup. Retry with the selected password.")
    vault.update_secret(secret_id, oci.vault.models.UpdateSecretDetails(
        current_version_number=pending.version_number), if_match=meta.headers["etag"])
    _ready(vault, secret_id)
