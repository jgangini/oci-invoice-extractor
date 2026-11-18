"""Read-only database compatibility checks and an installer ownership claim."""

from hashlib import sha256
import re


class DatabaseTargetError(ValueError):
    """Safe, actionable error for Studio's deployment log."""


def existing_dsn(database, compartment_id=None):
    if compartment_id and database.compartment_id != compartment_id:
        raise DatabaseTargetError("The selected database belongs to a different compartment.")
    database_profile = (database.lifecycle_state, database.db_workload, database.db_version,
                        database.open_mode, database.permission_level)
    if database.is_dedicated or database_profile != ("AVAILABLE", "OLTP", "26ai", "READ_WRITE", "UNRESTRICTED"):
        raise DatabaseTargetError("Select an available 26ai serverless OLTP database with unrestricted read-write access.")
    restricted_network = any((database.private_endpoint, database.is_access_control_enabled, database.whitelisted_ips))
    if database.is_mtls_connection_required is not False or restricted_network:
        raise DatabaseTargetError("The existing database must already accept walletless TLS from the worker and the new VCN.")
    enabled = {tool.name for tool in database.db_tools_details or [] if tool.is_enabled}
    apex_url = getattr(database.connection_urls, "apex_url", None)
    if not {"APEX", "ORDS"} <= enabled or not (apex_url or "").startswith("https://"):
        raise DatabaseTargetError("Select a database with APEX and ORDS already enabled.")
    return _low_service_dsn(database.connection_strings)


def _low_service_dsn(connection_strings):
    profiles = getattr(connection_strings, "profiles", None) or []
    dsns = [p.value for p in profiles if p.value and
            (p.tls_authentication, p.consumer_group, p.host_format, p.protocol) == ("SERVER", "LOW", "FQDN", "TCPS")]
    if len(dsns) != 1:
        raise DatabaseTargetError("The database must advertise one walletless TLS LOW service.")
    return dsns[0]


def check_existing_principal(admin):
    """APEX reprocessing needs the database's existing OCI resource principal."""
    with admin.cursor() as cur:
        cur.execute("select count(*) from dba_credentials where owner='ADMIN' and credential_name='OCI$RESOURCE_PRINCIPAL'")
        if not cur.fetchone()[0]:
            raise DatabaseTargetError("Select a database with its ADMIN OCI resource principal already enabled for APEX reprocessing.")


def _check_apex_installer(cur):
    cur.execute("select count(*) from session_roles where role='APEX_ADMINISTRATOR_ROLE'")
    if not cur.fetchone()[0]:
        raise DatabaseTargetError("The session needs APEX_ADMINISTRATOR_ROLE to check ownership across the instance.")
    cur.execute("select version_no from apex_release")
    version = re.match(r"^(\d+)\.(\d+)", str(cur.fetchone()[0]))
    if not version or tuple(map(int, version.groups())) < (26, 1):
        raise DatabaseTargetError("The invoice application export requires APEX 26.1 or later.")


def _has_installation_claim(cur, owner):
    cur.execute("select object_type from user_objects where object_name='INV_EXTRACTOR_INSTALL'")
    marker = cur.fetchall()
    if not marker:
        return False
    if marker != [("TABLE",)]:
        raise DatabaseTargetError("The installation marker name already belongs to a different database object.")
    cur.execute("select deployment_hash from admin.inv_extractor_install")
    if cur.fetchall() != [(owner,)]:
        raise DatabaseTargetError("This database is already reserved by another deployment.")
    return True


def check_install_target(admin, deployment_id, *, reserve=False):
    """Reject namespace collisions before DDL. A matching deployment can resume partial installation."""
    if not isinstance(deployment_id, str) or not deployment_id.strip():
        raise DatabaseTargetError("The Deploy Studio deployment ID is missing.")
    owner = sha256(deployment_id.encode()).hexdigest()
    with admin.cursor() as cur:
        _check_apex_installer(cur)
        marker = _has_installation_claim(cur, owner)
        cur.execute("select count(*) from dba_users where username in ('INV_DATA','INV_FN','INV_APEX')")
        schema_count = cur.fetchone()[0]
        cur.execute("select workspace from apex_workspaces where workspace='INVOICES'")
        workspace = cur.fetchone()
        cur.execute("""select application_id, workspace, owner, alias from apex_applications
            where application_id=100 or (workspace='INVOICES' and upper(alias)='INVOICE-EXTRACTOR')""")
        apps = cur.fetchall()
        if not marker and (schema_count or workspace or apps):
            raise DatabaseTargetError("The invoice schemas, workspace INVOICES or application ID 100 are already in use.")
        if workspace:
            cur.execute("select schema from apex_workspace_schemas where workspace_name='INVOICES'")
            if cur.fetchall() != [("INV_APEX",)]:
                raise DatabaseTargetError("The INVOICES workspace has unexpected schema mappings.")
        if apps and apps != [(100, "INVOICES", "INV_APEX", "INVOICE-EXTRACTOR")]:
            raise DatabaseTargetError("The selected application ID or alias belongs to a different installation.")
        if reserve and not marker:
            # CTAS claims the namespace atomically. Only a fixed-length hexadecimal digest enters DDL.
            cur.execute(f"create table admin.inv_extractor_install as select '{owner}' deployment_hash from dual")
