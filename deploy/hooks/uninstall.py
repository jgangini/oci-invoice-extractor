"""Remove only the application owned by this installation inside a reused database."""
from hashlib import sha256

import oracledb
import oci

from deploy.hooks.database_target import (
    DatabaseTargetError, SCHEMAS, _has_installation_claim, installation_profile, schema_identity,
)
from deploy.hooks.preflight import config_for, context_file
from deploy.hooks.post_apply import delivery


def remove_installation(admin, deployment_id):
    owner = sha256(deployment_id.encode()).hexdigest()
    with admin.cursor() as cur:
        if not _has_installation_claim(cur, owner):
            # No DDL runs before the installation claim. A completed cleanup also removes it last.
            return
        profile, profile_exists = installation_profile(cur)
        users = {name: schema_identity(cur, name, profile)[1] for name in reversed(SCHEMAS)}
        cur.execute("select workspace_id from admin.inv_extractor_install")
        workspace_id, = cur.fetchone()
        cur.execute("select workspace_id from apex_workspaces where workspace='INVOICES'")
        workspace = cur.fetchone()
        if workspace:
            if workspace != (workspace_id,):
                raise DatabaseTargetError("Workspace ownership has changed.")
            cur.execute("select schema from apex_workspace_schemas where workspace_name='INVOICES'")
            if cur.fetchall() != [("INV_APEX",)]:
                raise DatabaseTargetError("The workspace now maps other schemas.")
            cur.execute("select application_id, owner, alias from apex_applications where workspace='INVOICES'")
            if any(app != (100, "INV_APEX", "INVOICE-EXTRACTOR") for app in cur):
                raise DatabaseTargetError("The workspace now contains another application.")
            cur.execute("""begin apex_instance_admin.remove_workspace(p_workspace=>'INVOICES',
                        p_drop_users=>'N', p_drop_tablespaces=>'N'); end;""")
        for name, user_id in users.items():
            if user_id is not None:
                if schema_identity(cur, name, profile)[1] != user_id:
                    raise DatabaseTargetError("Database user ownership changed during cleanup.")
                cur.execute(f"drop user {name} cascade")
        cur.execute("""select (select count(*) from dba_users where username in ('INV_FN','INV_APEX','INV_DATA'))
                       + (select count(*) from apex_workspaces where workspace='INVOICES') from dual""")
        if cur.fetchone()[0]:
            raise DatabaseTargetError("Application cleanup remains incomplete.")
        if profile_exists:
            cur.execute(f"drop profile {profile}")
        cur.execute("drop table admin.inv_extractor_install purge")


def main():
    context = context_file("DEPLOY_STUDIO_CONTEXT")
    secret_inputs = context_file("DEPLOY_STUDIO_SECRETS")["inputs"]
    password = secret_inputs["autonomous_database_admin_password"]
    outputs = context["terraform_outputs"]
    with oracledb.connect(user="ADMIN", password=password, dsn=outputs["db_dsn"],
                          ssl_server_dn_match=True, tcp_connect_timeout=10) as admin:
        delivery(oci.events.EventsClient(config_for(context["region"])), outputs, outputs["bucket_compartment_id"], False)
        remove_installation(admin, context["deployment_id"])


if __name__ == "__main__":
    main()
