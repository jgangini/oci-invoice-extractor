"""Create a portable OCI layout from the publisher's digest-pinned OCIR image."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile

import oci
import urllib3


def add(archive, name, length, data):
    entry = tarfile.TarInfo(name)
    entry.size = length
    entry.mode = 0o444
    archive.addfile(entry, data)


def write_layout(cfg, repository, digest, destination):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not re.fullmatch(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*", repository):
        raise ValueError("Use an exact OCIR repository and SHA-256 digest")
    namespace = oci.object_storage.ObjectStorageClient(cfg).get_namespace().data
    host = f"ocir.{cfg['region']}.oci.oraclecloud.com"
    path = namespace + "/" + repository
    token = oci.container_registry.ContainerRegistryClient(cfg).get_access_token(service=host, scope=f"repository:{path}:pull").data.token
    with urllib3.PoolManager() as session:
        headers = {"Authorization": "Bearer " + token}
        base = f"https://{host}/v2/{path}"
        response = session.request("GET", f"{base}/manifests/{digest}", headers=headers | {"Accept":"application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"}, timeout=30)
        if response.status != 200:
            raise ValueError("Registry manifest unavailable")
        raw = response.data
        if hashlib.sha256(raw).hexdigest() != digest[7:]:
            raise ValueError("Registry manifest does not match requested digest")
        manifest = response.json()
        image = {"mediaType":manifest["mediaType"], "digest":digest, "size":len(raw)}
        index = json.dumps({"schemaVersion":2, "manifests":[image]}, separators=(",",":")).encode()
        with tarfile.open(fileobj=destination, mode="w") as archive:
            for name, data in (("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'), ("index.json", index), ("blobs/sha256/"+digest[7:], raw)):
                add(archive, name, len(data), io.BytesIO(data))
            for blob in [manifest["config"], *manifest["layers"]]:
                key = blob["digest"]
                if not re.fullmatch("sha256:[0-9a-f]{64}", key):
                    raise ValueError("Unexpected image descriptor")
                with session.request("GET", f"{base}/blobs/{key}", headers=headers, preload_content=False, decode_content=False,
                                     timeout=urllib3.Timeout(connect=15, read=300)) as stream:
                    if stream.status != 200:
                        raise ValueError("Registry blob unavailable")
                    add(archive, "blobs/sha256/"+key[7:], blob["size"], stream)


def export(cfg, repository, digest, output):
    from deploy.hooks.prepare_image import layout
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temp:
        path = Path(temp.name)
        try:
            write_layout(cfg, repository, digest, temp)
            temp.flush()
            temp.seek(0)
            with tarfile.open(fileobj=temp, mode="r:") as archive:
                layout(archive)
            temp.close()
            os.link(path, output)
        finally:
            temp.close()
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("config", "key", "repository", "digest", "output"):
        parser.add_argument("--"+field, required=True)
    args = parser.parse_args()
    cfg = oci.config.from_file(str(Path(args.config).resolve()))
    cfg["key_file"] = str(Path(args.key).resolve())
    export(cfg, args.repository, args.digest, Path(args.output))
