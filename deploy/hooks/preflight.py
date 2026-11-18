"""Validate release inputs from the pinned Deploy Studio source before provisioning."""

import base64
import ipaddress
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SQL_FILES = ("schema", "api", "grants", "seed")


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


def check_inputs(context):
    import oci

    inputs = context["inputs"]
    if inputs["runtime_secret_ocid"] == inputs["admin_secret_ocid"]:
        raise InputError("Los usuarios ADMIN e INV_FN requieren secretos distintos.")
    for cidr in inputs["access_cidrs"].split(","):
        network = ipaddress.ip_network(cidr.strip())
        if network.version != 4 or network.prefixlen < 24 or not network.is_global:
            raise InputError("Indica las redes IPv4 públicas autorizadas /24 a /32.")
    for path in [ROOT / "database" / f"{name}.sql" for name in SQL_FILES] + [ROOT / "apex/f100.sql"]:
        if not path.is_file() or not path.stat().st_size:
            raise InputError(f"Falta el archivo de instalación {path.name}.")
    cfg = config_for(context["region"])
    image = oci.artifacts.ArtifactsClient(cfg).get_container_image(inputs["function_image_ocid"]).data
    if (image.digest != inputs["function_image_digest"] or image.lifecycle_state != "AVAILABLE"
            or not image.version or image.version == "latest"):
        raise InputError("La imagen de la región seleccionada debe coincidir con el digest y tener una etiqueta de versión.")
    secrets = oci.secrets.SecretsClient(cfg)
    admin = read_password(secrets, inputs["admin_secret_ocid"])
    runtime = read_password(secrets, inputs["runtime_secret_ocid"])
    if admin == runtime or not password_valid(admin, "ADMIN") or not password_valid(runtime, "INV_FN"):
        raise InputError("Configura contraseñas distintas y compatibles en los dos secretos de Vault.")


def main():
    path = Path(os.environ["DEPLOY_STUDIO_RESULT"])
    try:
        check_inputs(context_file("DEPLOY_STUDIO_CONTEXT"))
        status, message = "passed", "Imagen regional, secretos, ACL y archivos de la release verificados."
    except Exception as exc:
        # Keep OCI error bodies and Vault values outside the deployment history.
        status = "failed"
        message = str(exc) if isinstance(exc, InputError) else "Comprueba imagen OCIR, permisos y contenido de Vault, redes de acceso y archivos de la release."
    path.write_text(json.dumps({"events": [{"name": "Invoice Extractor", "status": status, "message": message}]}), encoding="utf-8")
    return int(status == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
