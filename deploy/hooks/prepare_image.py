"""Publish the immutable release image to a private regional OCIR repository."""

import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import time
from urllib.parse import urljoin, urlsplit

import oci

from deploy.hooks.preflight import config_for, context_file


ASSET = "function-image.tar"
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
IMAGE_TYPES = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}


def find_or_create_repository(artifacts, compartment, name, create):
    matches = artifacts.list_container_repositories(compartment, display_name=name).data.items
    if matches:
        if len(matches) != 1:
            raise ValueError("Ambiguous image repository")
        return artifacts.get_container_repository(matches[0].id).data
    if create:
        details = oci.artifacts.models.CreateContainerRepositoryDetails(
            compartment_id=compartment, display_name=name, is_public=False)
        try:
            return artifacts.create_container_repository(details).data
        except oci.exceptions.ServiceError as exc:
            if exc.status != 409:
                raise
    return None


def private_repository(artifacts, compartment, name):
    for attempt in range(12):
        repository = find_or_create_repository(artifacts, compartment, name, create=attempt == 0)
        if repository is not None:
            if (repository.display_name, repository.compartment_id) != (name, compartment) or repository.is_public is not False:
                raise ValueError("Expected a private repository in the selected compartment")
            if repository.lifecycle_state == "AVAILABLE":
                return repository
        if attempt < 11:
            time.sleep(5)
    raise ValueError("Image repository is unavailable")


def archive_entries(archive):
    entries = {}
    for entry in archive:
        if len(entries) >= 192 or not entry.isfile() or entry.name in entries or not re.fullmatch(
                r"(?:index\.json|oci-layout|blobs/sha256/[a-f0-9]{64})", entry.name):
            raise ValueError("Invalid OCI archive member")
        entries[entry.name] = entry

    return entries


def descriptor(entries, item):
    match = DIGEST.fullmatch(item["digest"])
    if not match or type(item["size"]) is not int:
        raise ValueError("Invalid OCI descriptor")
    entry = entries["blobs/sha256/" + match[1]]
    if entry.size != item["size"]:
        raise ValueError("Invalid OCI descriptor size")
    return entry


def metadata(archive, entry):
    if entry.size > 1024 * 1024:
        raise ValueError("OCI metadata exceeds size limit")
    with archive.extractfile(entry) as stream:
        return stream.read()


def layout(archive):
    entries = archive_entries(archive)
    if json.loads(metadata(archive, entries["oci-layout"])) != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("Unsupported OCI image layout")
    index = json.loads(metadata(archive, entries["index.json"]))
    if index.get("schemaVersion") != 2 or len(index["manifests"]) != 1:
        raise ValueError("Expected one release image")
    image = index["manifests"][0]
    manifest = metadata(archive, descriptor(entries, image))
    if image["mediaType"] not in IMAGE_TYPES or hashlib.sha256(manifest).hexdigest() != image["digest"][7:]:
        raise ValueError("Image manifest digest mismatch")
    parsed = json.loads(manifest)
    if parsed.get("schemaVersion") != 2 or parsed.get("mediaType") != image["mediaType"]:
        raise ValueError("Invalid image manifest")
    blobs = [parsed["config"], *parsed["layers"]]
    expected = {descriptor(entries, blob).name for blob in blobs} | {descriptor(entries, image).name, "index.json", "oci-layout"}
    if entries.keys() != expected:
        raise ValueError("OCI image contains unreferenced files")
    for blob in blobs:
        entry = descriptor(entries, blob)
        with archive.extractfile(entry) as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != blob["digest"][7:]:
                raise ValueError("OCI blob digest mismatch")
    config = json.loads(metadata(archive, descriptor(entries, parsed["config"])))
    if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
        raise ValueError("Release requires a linux/amd64 image")
    return image, manifest, [(blob, descriptor(entries, blob)) for blob in blobs]


def upload_location(response, base):
    result = urljoin(base + "/", response.headers["Location"])
    actual, expected = urlsplit(result), urlsplit(base)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or not actual.path.startswith(expected.path + "/blobs/uploads/"):
        raise ValueError("Registry returned an unexpected upload location")
    return result


def upload_blob(session, base, archive, digest, entry):
    exists = session.head(f"{base}/blobs/{digest}", timeout=30)
    if exists.status_code == 200:
        return
    if exists.status_code != 404:
        raise ValueError("Failed to check registry blob")
    start = session.post(base + "/blobs/uploads/", timeout=30)
    if start.status_code != 202:
        raise ValueError("Registry rejected upload initiation")
    with archive.extractfile(entry) as stream:
        location = upload_location(start, base)
        uploaded = session.put(location + ("&" if "?" in location else "?") + "digest=" + digest, content=stream,
                               headers={"Content-Type": "application/octet-stream", "Content-Length": str(entry.size)},
                               timeout=300)
    if uploaded.status_code != 201:
        raise ValueError("Registry rejected image blob")


def publish(session, base, archive, parsed, tag):
    image, manifest, blobs = parsed
    tag_url = f"{base}/manifests/{tag}"
    existing = session.head(tag_url, headers={"Accept": image["mediaType"]}, timeout=30)
    if existing.status_code == 200:
        if existing.headers.get("Docker-Content-Digest") != image["digest"]:
            raise ValueError("The version tag already points to a different release")
        return
    if existing.status_code != 404:
        existing.raise_for_status()
        raise ValueError("Unexpected registry response")
    for blob, entry in blobs:
        upload_blob(session, base, archive, blob["digest"], entry)
    put = session.put(tag_url, content=manifest, headers={"Content-Type": image["mediaType"]}, timeout=60)
    if put.status_code != 201 or put.headers.get("Docker-Content-Digest") != image["digest"]:
        raise ValueError("Registry failed to verify the published manifest")


def prepare(context):
    import httpx
    cfg = config_for(context["region"])
    compartment, namespace = context["compartment_id"], context["namespace"]
    project = "oci-invoice-extractor"
    repo = project + "-" + hashlib.sha256(compartment.encode()).hexdigest()[:16]
    tag = context["commit_sha"]
    if not re.fullmatch("[a-f0-9]{40}", tag) or context["project_id"] != project:
        raise ValueError("Invalid pinned source")
    asset = context["release_assets"]["assets"][ASSET]
    with tarfile.open(asset["path"], mode="r:") as archive:
        parsed = layout(archive)
        artifacts = oci.artifacts.ArtifactsClient(cfg)
        repository = private_repository(artifacts, compartment, repo)
        host = f"ocir.{cfg['region']}.oci.oraclecloud.com"
        scope = f"repository:{namespace}/{repo}:pull,push"
        token = oci.container_registry.ContainerRegistryClient(cfg).get_access_token(service=host, scope=scope).data.token
        with httpx.Client(trust_env=False, follow_redirects=False, headers={"Authorization": "Bearer " + token}) as session:
            publish(session, f"https://{host}/v2/{namespace}/{repo}", archive, parsed, tag)
        for attempt in range(12):
            matches = artifacts.list_container_images(compartment, repository_id=repository.id, version=tag).data.items
            if len(matches) == 1 and matches[0].lifecycle_state == "AVAILABLE" and matches[0].digest == parsed[0]["digest"]:
                return {"function_image_ocid": matches[0].id, "function_image_digest": matches[0].digest}
            if attempt < 11:
                time.sleep(5)
        raise ValueError("Published image not available by digest")


if __name__ == "__main__":
    variables = prepare(context_file("DEPLOY_STUDIO_CONTEXT"))
    Path(os.environ["DEPLOY_STUDIO_RESULT"]).write_text(json.dumps({"variables": variables}), encoding="utf8")
