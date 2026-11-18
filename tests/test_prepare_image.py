import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from deploy.export_image import add
from deploy.hooks.prepare_image import layout, private_repository, publish


def image_archive(architecture="amd64", corrupt=False, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        def file(name, raw):
            add(archive, name, len(raw), io.BytesIO(raw))
        def blob(raw, media_type, bad=False):
            digest = hashlib.sha256(raw).hexdigest()
            file("blobs/sha256/" + digest, b"x" * len(raw) if bad else raw)
            return {"size": len(raw), "digest": "sha256:" + digest, "mediaType": media_type}
        config = blob(json.dumps({"os":"linux", "architecture":architecture}).encode(), "application/vnd.oci.image.config.v1+json")
        layer = blob(b"layer-content", "application/vnd.oci.image.layer.v1.tar+gzip", corrupt)
        media = "application/vnd.oci.image.manifest.v1+json"
        manifest = blob(json.dumps({"schemaVersion":2, "mediaType":media, "layers":[layer], "config":config}).encode(), media)
        file("index.json", json.dumps({"schemaVersion":2, "manifests":[manifest]}).encode())
        file("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        if extra:
            file(extra, b"unexpected")
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:")


class PrepareImageTests(unittest.TestCase):
    def test_archive_integrity(self):
        with image_archive() as archive:
            image, manifest, blobs = layout(archive)
            self.assertEqual(image["digest"], "sha256:" + hashlib.sha256(manifest).hexdigest())
            self.assertEqual(len(blobs), 2)
        for args in ({"architecture":"arm64"}, {"corrupt":True}, {"extra":"../key.pem"}):
            with self.subTest(args=args), image_archive(**args) as archive, self.assertRaises(ValueError):
                layout(archive)

    def test_push_missing_blobs_then_accept_exact_existing_tag(self):
        base = "https://registry.example/v2/ns/private"
        session = Mock()
        session.head.return_value = SimpleNamespace(status_code=404)
        session.post.return_value = SimpleNamespace(status_code=202, headers={"Location":"/v2/ns/private/blobs/uploads/id?state=s"})
        with image_archive() as archive:
            parsed = layout(archive)
            digest = parsed[0]["digest"]
            def put(url, *, content, headers, timeout):
                if "/blobs/" in url:
                    data = content.read()
                    self.assertTrue(url.endswith("?state=s&digest=sha256:" + hashlib.sha256(data).hexdigest()))
                    self.assertEqual(headers["Content-Length"], str(len(data)))
                else:
                    self.assertEqual(content, parsed[1])
                return SimpleNamespace(status_code=201, headers={"Docker-Content-Digest":digest})
            session.put.side_effect = put
            publish(session, base, archive, parsed, "a" * 40)
            self.assertEqual(session.put.call_count, 3)
            session.reset_mock()
            session.head.return_value = SimpleNamespace(status_code=200, headers={"Docker-Content-Digest":digest})
            publish(session, base, archive, parsed, "a" * 40)
            session.put.assert_not_called()
            session.head.return_value.headers = {"Docker-Content-Digest":"different"}
            with self.assertRaisesRegex(ValueError, "different release"):
                publish(session, base, archive, parsed, "a" * 40)

    def test_rejects_foreign_upload_location(self):
        session = Mock()
        session.head.return_value = SimpleNamespace(status_code=404)
        session.post.return_value = SimpleNamespace(status_code=202, headers={"Location":"https://untrusted.example/v2/ns/name/blobs/uploads/id"})
        with image_archive() as archive, self.assertRaisesRegex(ValueError, "location"):
            publish(session, "https://registry.example/v2/ns/name", archive, layout(archive), "a" * 40)
        session.put.assert_not_called()

    def test_private_immutable_repository_created_or_verified_with_get(self):
        api = Mock()
        api.list_container_repositories.return_value.data.items = []
        repo = SimpleNamespace(id="id", compartment_id="target", display_name="repo", is_public=False,
                               is_immutable=True, lifecycle_state="AVAILABLE")
        api.create_container_repository.return_value.data = repo
        self.assertIs(private_repository(api, "target", "repo"), repo)
        details = api.create_container_repository.call_args.args[0]
        self.assertTrue(details.is_immutable)
        self.assertFalse(details.is_public)
        api.list_container_repositories.return_value.data.items = [SimpleNamespace(id="id")]
        api.get_container_repository.return_value.data = repo
        self.assertIs(private_repository(api, "target", "repo"), repo)
        repo.is_public = True
        with self.assertRaisesRegex(ValueError, "private"):
            private_repository(api, "target", "repo")


if __name__ == "__main__":
    unittest.main()
