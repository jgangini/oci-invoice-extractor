"""Install the pinned SQL/APEX payload, then activate its scoped Events rule."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

from deploy.hooks.database_target import DatabaseTargetError, SCHEMAS, installation_profile, schema_identity
from deploy.hooks.preflight import ROOT, config_for, context_file, password_valid, read_password


WORKSPACE = "INVOICES"


def units(script):
    """Each reviewed database script uses a slash on its own line per OCI statement."""
    blocks = re.split(r"(?m)^\s*/\s*$", script)
    if blocks[-1].strip():
        raise ValueError("Database script is missing its final slash delimiter")
    for block in blocks[:-1]:
        sql = block.strip()
        if sql:
            if not re.match(r"(?is)^(?:begin|declare|create\s+(?:or\s+replace\s+)?(?:package|procedure|function|trigger|type))\b", sql):
                sql = sql.removesuffix(";")
            yield sql


def execute_script(cursor, name, **binds):
    for sql in units((ROOT / "database" / f"{name}.sql").read_text(encoding="utf-8-sig")):
        cursor.execute(sql, binds)


def scalar(cursor, sql, **values):
    cursor.execute(sql, values)
    return cursor.fetchone()[0]


def prepare_account(cur, name, profile, runtime_password):
    recorded, actual = schema_identity(cur, name, profile)
    if actual is None:
        if recorded is not None:
            raise DatabaseTargetError("A recorded installation account was removed.")
        if name == "INV_FN":
            cur.execute(f"""begin execute immediate 'create user INV_FN identified by ' ||
                dbms_assert.enquote_name(:pw, false) || ' profile {profile}'; end;""",
                pw='"' + runtime_password + '"')
        else:
            cur.execute(f"create user {name} no authentication profile {profile}")
        _, actual = schema_identity(cur, name, profile)
    if recorded is None:
        # The profile identifies a CREATE USER whose reply or following commit was lost.
        cur.execute(f"""update admin.inv_extractor_install set {name}_id=:user_id
            where {name}_id is null and exists (select 1 from dba_users
            where username=:name and user_id=:user_id and profile=:profile)""",
            user_id=actual, name=name, profile=profile)
        if cur.rowcount != 1:
            raise DatabaseTargetError("Could not verify the newly created database account.")
    cur.execute(f"grant create session to {name}")


def prepare_users(admin, runtime_password):
    if not password_valid(runtime_password, "INV_FN"):
        raise ValueError("Invalid runtime password")
    with admin.cursor() as cur:
        profile, exists = installation_profile(cur)
        if not exists:
            cur.execute(f"create profile {profile} limit failed_login_attempts default")
        for name in SCHEMAS:
            prepare_account(cur, name, profile, runtime_password)
        cur.execute("alter user INV_DATA quota unlimited on DATA")
        cur.execute("grant create table, create view, create procedure, create sequence to INV_DATA")
        cur.execute("grant execute on DBMS_CLOUD to INV_DATA")


@contextmanager
def schema_session(admin, schema, password, dsn):
    """Run DDL as its real owner without a persistent password on the owner account."""
    import oracledb

    if schema not in {"INV_DATA", "INV_APEX"}:
        raise ValueError("Unknown installer schema")
    with admin.cursor() as cursor:
        cursor.execute(f"alter user {schema} grant connect through ADMIN")
        try:
            with oracledb.connect(user=f"ADMIN[{schema}]", password=password, dsn=dsn, ssl_server_dn_match=True) as session:
                yield session
        finally:
            cursor.execute(f"alter user {schema} revoke connect through ADMIN")


def regional_endpoints(region, outputs):
    from oci import regions

    if region not in regions.REGION_REALMS:
        raise ValueError("Unsupported OCI region for database HTTP access")
    domain = regions.REALMS[regions.REGION_REALMS[region]]
    storage_host = f"objectstorage.{region}.{domain}"
    endpoint = regions.endpoint_for("object_storage", region=region)
    if endpoint != f"https://{storage_host}":
        raise ValueError("Unsupported regional Object Storage endpoint")
    opaque = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    regional_service = rf"(?:functions\.{re.escape(region)}|{re.escape(region)}\.functions)"
    function_host = rf"{opaque}\.{regional_service}\.oci\.{re.escape(domain)}"
    invoke = re.fullmatch(rf"https://({function_host})(?::443)?/20181201/functions/"
                          r"(ocid1\.fnfunc\.[A-Za-z0-9._-]+)/actions/invoke", outputs["function_invoke_url"])
    if not invoke or invoke[2] != outputs["function_id"]:
        raise ValueError("Functions invocation URL does not match the selected region and function")
    namespace = quote(outputs["object_namespace"], safe="")
    bucket = quote(outputs["bucket_name"], safe="")
    return (invoke[1], storage_host), f"{endpoint}/n/{namespace}/b/{bucket}/o/"


def enable_retry(admin, region, outputs, *, existing_database=False):
    hosts, object_base_url = regional_endpoints(region, outputs)
    with admin.cursor() as cur:
        if not scalar(cur, "select count(*) from dba_credentials where owner='ADMIN' and credential_name='OCI$RESOURCE_PRINCIPAL'"):
            if existing_database:
                raise ValueError("Enable the database resource principal before reusing this database.")
            cur.execute("begin dbms_cloud_admin.enable_resource_principal; end;")
        if not scalar(cur, """select count(*) from all_tab_privs where grantee='INV_DATA'
                and table_schema='ADMIN' and table_name='OCI$RESOURCE_PRINCIPAL'"""):
            cur.execute("begin dbms_cloud_admin.enable_resource_principal(username=>'INV_DATA'); end;")
        for hostname in hosts:
            cur.execute("""begin dbms_network_acl_admin.append_host_ace(host=>:host,
                lower_port=>443, upper_port=>443, ace=>xs$ace_type(privilege_list=>xs$name_list('http'),
                principal_name=>'INV_DATA',principal_type=>xs_acl.ptype_db)); end;""", host=hostname)
    return object_base_url


def install_data(session, outputs, operator, object_base_url, catalog):
    import oracledb

    with session.cursor() as cur:
        for name in ("schema", "api", "uploads", "grants"):
            execute_script(cur, name)
        cur.execute("""merge into inv_settings t using
            (select 1 id,:url invoke_url,:prefix source_prefix,:base object_base_url from dual) s on (t.id=s.id)
            when matched then update set t.invoke_url=s.invoke_url,t.source_prefix=s.source_prefix,t.object_base_url=s.object_base_url
            when not matched then insert(id,invoke_url,source_prefix,object_base_url)
            values(s.id,s.invoke_url,s.source_prefix,s.object_base_url)""",
            url=outputs["function_invoke_url"], prefix=outputs["source_prefix"], base=object_base_url)
        cur.setinputsizes(catalog=oracledb.DB_TYPE_CLOB)
        execute_script(cur, "seed", catalog=catalog)
        cur.execute("""merge into inv_operator t using (select :name username from dual) s on (t.username=s.username)
            when not matched then insert(username,permission) values(s.username,'ADMIN')""", name=operator)
        if scalar(cur, "select count(*) from user_objects where status='INVALID'"):
            raise RuntimeError("Invalid database objects")
    session.commit()


def create_workspace(admin, operator, password):
    with admin.cursor() as cur:
        if not scalar(cur, "select count(*) from apex_workspaces where workspace=:ws", ws=WORKSPACE):
            workspace_id = scalar(cur, "select workspace_id from admin.inv_extractor_install")
            cur.execute("""begin apex_instance_admin.add_workspace(p_workspace_id=>:ws_id,p_workspace=>:ws,
                p_primary_schema=>'INV_APEX', p_additional_schemas=>null); end;""",
                ws_id=workspace_id, ws=WORKSPACE)
        if not scalar(cur, """select count(*) from admin.inv_extractor_install i join apex_workspaces w
                            on w.workspace_id=i.workspace_id where w.workspace=:ws""", ws=WORKSPACE):
            raise ValueError("The APEX workspace identity differs from the installation record.")
        cur.execute("begin apex_util.set_workspace(p_workspace=>:ws); end;", ws=WORKSPACE)
        if scalar(cur, "select apex_util.get_user_id(:name) from dual", name=operator) is None:
            cur.execute("""begin apex_util.create_user(p_user_name=>:name,p_web_password=>:pw,
                p_developer_privs=>null,p_change_password_on_first_use=>'N'); end;""", name=operator, pw=password)
    admin.commit()


def install_apex(session):
    import oracledb

    export = (ROOT / "apex" / "f100.sql").read_text(encoding="utf-8-sig")
    with session.cursor() as cur:
        cur.execute("begin apex_util.set_workspace(p_workspace=>:ws); end;", ws=WORKSPACE)
        cur.setinputsizes(export=oracledb.DB_TYPE_CLOB)
        cur.execute("""begin
            apex_application_install.set_workspace(p_workspace=>:ws);
            apex_application_install.set_schema('INV_APEX');
            apex_application_install.set_application_id(100);
            apex_application_install.install(p_source=>apex_t_export_files(
                apex_t_export_file(name=>'f100.sql',contents=>:export)),p_overwrite_existing=>true);
            end;""", ws=WORKSPACE, export=export)
        if not scalar(cur, """select count(*) from apex_applications where application_id=100
                            and workspace=:ws and owner='INV_APEX'""", ws=WORKSPACE):
            raise RuntimeError("Application installation did not complete")
    session.commit()


def delivery(client, outputs, compartment, enabled):
    import oci

    rule = client.get_rule(outputs["event_rule_id"])
    actions = rule.data.actions.actions
    if rule.data.compartment_id != compartment or len(actions) != 1 or actions[0].function_id != outputs["function_id"]:
        raise ValueError("Events destination differs from the selected deployment")
    if rule.data.is_enabled != enabled:
        client.update_rule(rule.data.id, oci.events.models.UpdateRuleDetails(is_enabled=enabled),
                           if_match=rule.headers["etag"])


def application_url(base):
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname or "/ords" not in parts.path:
        raise ValueError("ADB did not return an ORDS application URL")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.split("/ords", 1)[0] + "/ords/f", "p=100:1", ""))


def put_initial_catalog(storage, outputs, catalog):
    import oci

    key = (outputs["object_namespace"], outputs["bucket_name"], outputs["source_prefix"] + "invoice-catalog.json")
    payload = catalog.encode("utf-8")
    try:
        storage.put_object(*key, payload, content_type="application/json", if_none_match="*")
    except oci.exceptions.ServiceError as exc:
        if exc.status != 412:
            raise
        existing = storage.get_object(*key, range=f"bytes=0-{len(payload)}")
        try:
            if existing.data.content != payload:
                raise ValueError("The initial catalog object already has different content.")
        finally:
            existing.data.close()


def install(stages):
    import oci
    import oracledb

    context = context_file("DEPLOY_STUDIO_CONTEXT")
    outputs = context["terraform_outputs"]
    inputs = context["inputs"]
    cfg = config_for(context["region"])
    operator = inputs["apex_operator"].upper()
    from deploy.hooks.database_target import check_install_target
    from deploy.hooks.admin_password import configure_admin_password
    secret_inputs = context_file("DEPLOY_STUDIO_SECRETS")["inputs"]
    operator_password = secret_inputs["apex_operator_password"]
    secrets = oci.secrets.SecretsClient(cfg)
    events = oci.events.EventsClient(cfg)
    storage = oci.object_storage.ObjectStorageClient(cfg)
    catalog = (ROOT / "source/invoice-catalog.json").read_text(encoding="utf-8")
    stages.append("target and Vault verification")
    delivery(events, outputs, outputs["bucket_compartment_id"], False)
    admin_password = secret_inputs["autonomous_database_admin_password"]
    runtime_password = read_password(secrets, outputs["runtime_secret_ocid"])
    dsn = outputs["db_dsn"]
    stages.append("database admin credential setup")
    configure_admin_password(cfg, inputs["autonomous_database_mode"], outputs, admin_password)
    stages.append("Autonomous Database connection and schema setup")
    with oracledb.connect(user="ADMIN", password=admin_password, dsn=dsn, ssl_server_dn_match=True) as admin:
        check_install_target(admin, context["deployment_id"], reserve=True)
        prepare_users(admin, runtime_password)
        stages.append("resource principal and HTTPS access setup")
        object_base_url = enable_retry(admin, context["region"], outputs,
                                       existing_database=inputs["autonomous_database_mode"] == "existing")
        stages.append("data model and template installation")
        with schema_session(admin, "INV_DATA", admin_password, dsn) as owner:
            install_data(owner, outputs, operator, object_base_url, catalog)
        stages.append("APEX workspace and user setup")
        create_workspace(admin, operator, operator_password)
        stages.append("APEX application import")
        with schema_session(admin, "INV_APEX", admin_password, dsn) as parsing_schema:
            install_apex(parsing_schema)
    stages.append("runtime verification and event activation")
    with oracledb.connect(user="INV_FN", password=runtime_password, dsn=dsn, ssl_server_dn_match=True) as runtime:
        runtime.ping()
    url = application_url(outputs["apex_base_url"])
    if inputs["bucket_mode"] == "new":
        stages.append("initial catalog upload")
        put_initial_catalog(storage, outputs, catalog)
    stages.append("event activation")
    delivery(events, outputs, outputs["bucket_compartment_id"], True)
    return {"application_url": url}


def main():
    import oci
    stages = ["installation contract"]
    try:
        outputs = install(stages)
        result = {"outputs": outputs, "events": [{"level": "success", "message": "APEX installed and Events rule enabled."}]}
        code = 0
    except Exception as exc:
        # Only known error codes are useful here. Driver messages can include SQL or credentials.
        match = re.search(r"\b(?:ORA|DPY|PLS)-\d{4,5}\b", str(exc))
        error = f" ({match.group()})" if match else ""
        if stages[-1] == "database admin credential setup":
            from deploy.hooks.admin_password import AdminHandoffError
            if type(exc) is AdminHandoffError:
                error = f" ({str(exc).rstrip('.')})"
        if isinstance(exc, oci.exceptions.ServiceError):
            error = f" (OCI HTTP {exc.status})"
            if exc.code in {"Conflict", "IncorrectState", "NotAuthorizedOrNotFound", "InvalidParameter", "LimitExceeded", "TooManyRequests"}:
                error = f" (OCI HTTP {exc.status} {exc.code})"
        result = {"events": [{"level": "error", "message": f"Installation failed during {stages[-1]}{error}."}]}
        code = 1
    Path(os.environ["DEPLOY_STUDIO_OUTPUT"]).write_text(json.dumps(result), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
