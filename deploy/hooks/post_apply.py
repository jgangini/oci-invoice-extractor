"""Install the pinned SQL/APEX payload, then activate its scoped Events rule."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

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


def execute_script(cursor, name):
    for sql in units((ROOT / "database" / f"{name}.sql").read_text(encoding="utf-8-sig")):
        cursor.execute(sql)


def scalar(cursor, sql, **values):
    cursor.execute(sql, values)
    return cursor.fetchone()[0]


def prepare_users(admin, runtime_password):
    if not password_valid(runtime_password, "INV_FN"):
        raise ValueError("Invalid runtime password")
    with admin.cursor() as cur:
        for name in ("INV_DATA", "INV_APEX", "INV_FN"):
            exists = scalar(cur, "select count(*) from dba_users where username=:name", name=name)
            if not exists:
                if name == "INV_FN":
                    # Oracle DDL does not accept binds. DBMS_ASSERT quotes the validated secret inside PL/SQL.
                    cur.execute("""begin execute immediate 'create user INV_FN identified by ' ||
                        dbms_assert.enquote_name(:pw, false); end;""", pw='"' + runtime_password + '"')
                else:
                    cur.execute(f"create user {name} no authentication")
            cur.execute(f"grant create session to {name}")
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


def enable_retry(admin, invoke_url):
    host = urlsplit(invoke_url)
    if host.scheme != "https" or not (host.hostname or "").endswith(".functions.oci.oraclecloud.com"):
        raise ValueError("Unsupported Functions endpoint")
    with admin.cursor() as cur:
        if not scalar(cur, "select count(*) from dba_credentials where owner='ADMIN' and credential_name='OCI$RESOURCE_PRINCIPAL'"):
            cur.execute("begin dbms_cloud_admin.enable_resource_principal; end;")
        if not scalar(cur, """select count(*) from all_tab_privs where grantee='INV_DATA'
                and table_schema='ADMIN' and table_name='OCI$RESOURCE_PRINCIPAL'"""):
            cur.execute("begin dbms_cloud_admin.enable_resource_principal(username=>'INV_DATA'); end;")
        cur.execute("""begin dbms_network_acl_admin.append_host_ace(host=>:host,
            lower_port=>443, upper_port=>443, ace=>xs$ace_type(privilege_list=>xs$name_list('http'),
            principal_name=>'INV_DATA',principal_type=>xs_acl.ptype_db)); end;""", host=host.hostname)


def install_data(session, outputs, operator):
    with session.cursor() as cur:
        for name in ("schema", "api", "grants"):
            execute_script(cur, name)
        cur.execute("""merge into inv_settings t using
            (select 1 id,:url invoke_url,:prefix source_prefix from dual) s on (t.id=s.id)
            when matched then update set t.invoke_url=s.invoke_url,t.source_prefix=s.source_prefix
            when not matched then insert(id,invoke_url,source_prefix) values(s.id,s.invoke_url,s.source_prefix)""",
            url=outputs["function_invoke_url"], prefix=outputs["source_prefix"])
        execute_script(cur, "seed")
        cur.execute("""merge into inv_operator t using (select :name username from dual) s on (t.username=s.username)
            when not matched then insert(username,permission) values(s.username,'ADMIN')""", name=operator)
        if scalar(cur, "select count(*) from user_objects where status='INVALID'"):
            raise RuntimeError("Invalid database objects")
    session.commit()


def create_workspace(admin, operator, password):
    with admin.cursor() as cur:
        if not scalar(cur, "select count(*) from apex_workspaces where workspace=:ws", ws=WORKSPACE):
            cur.execute("""begin apex_instance_admin.add_workspace(p_workspace=>:ws,
                p_primary_schema=>'INV_APEX', p_additional_schemas=>null); end;""", ws=WORKSPACE)
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


def install(stages):
    import oci
    import oracledb

    context = context_file("DEPLOY_STUDIO_CONTEXT")
    outputs = context["terraform_outputs"]
    inputs = context["inputs"]
    cfg = config_for(context["region"])
    operator = inputs["apex_operator"].upper()
    operator_password = context_file("DEPLOY_STUDIO_SECRETS")["inputs"]["apex_operator_password"]
    secrets = oci.secrets.SecretsClient(cfg)
    events = oci.events.EventsClient(cfg)
    stages.append("verificación del destino y Vault")
    delivery(events, outputs, context["compartment"]["id"], False)
    admin_password = read_password(secrets, inputs["admin_secret_ocid"])
    runtime_password = read_password(secrets, inputs["runtime_secret_ocid"])
    dsn = outputs["db_dsn"]
    stages.append("conexión TLS a Autonomous Database desde el worker y creación de usuarios")
    with oracledb.connect(user="ADMIN", password=admin_password, dsn=dsn, ssl_server_dn_match=True) as admin:
        prepare_users(admin, runtime_password)
        stages.append("credencial de recurso y lista de acceso HTTPS")
        enable_retry(admin, outputs["function_invoke_url"])
        stages.append("instalación del modelo y las plantillas")
        with schema_session(admin, "INV_DATA", admin_password, dsn) as owner:
            install_data(owner, outputs, operator)
        stages.append("creación del workspace y usuario APEX")
        create_workspace(admin, operator, operator_password)
        stages.append("importación de la aplicación APEX")
        with schema_session(admin, "INV_APEX", admin_password, dsn) as parsing_schema:
            install_apex(parsing_schema)
    stages.append("validación de INV_FN y activación de Events")
    with oracledb.connect(user="INV_FN", password=runtime_password, dsn=dsn, ssl_server_dn_match=True) as runtime:
        runtime.ping()
    url = application_url(outputs["apex_base_url"])
    delivery(events, outputs, context["compartment"]["id"], True)
    return {"application_url": url}


def main():
    stages = ["lectura del contrato de instalación"]
    try:
        outputs = install(stages)
        result = {"outputs": outputs, "events": [{"level": "success", "message": "APEX instalado y regla de Events activa."}]}
        code = 0
    except Exception as exc:
        # Only known error codes are useful here. Driver messages can include SQL or credentials.
        match = re.search(r"\b(?:ORA|DPY|PLS)-\d{4,5}\b", str(exc))
        error = f" ({match.group()})" if match else ""
        result = {"events": [{"level": "error", "message": f"Falló la etapa de {stages[-1]}{error}."}]}
        code = 1
    Path(os.environ["DEPLOY_STUDIO_OUTPUT"]).write_text(json.dumps(result), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
