"""Manifest → JSON: the schema the web UIs and the torrent builder depend on."""

from __future__ import annotations

from conftest import make_symlink
from local_file_sharer.transfer.manifest import Manifest


def test_manifest_urls_never_double_the_slash(share_tree):
    """`host:port//download/x` is what you get if base_url keeps its trailing slash."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    for base in ("http://192.168.1.42:8080", "http://192.168.1.42:8080/"):
        payload = manifest.as_dict(base)
        for entry in payload["files"] + payload["items"]:
            for key in ("url", "download_url"):
                if key in entry:
                    assert "//" not in entry[key].split("://", 1)[1], entry[key]
                    assert entry[key].startswith("http://192.168.1.42:8080/")


def test_no_base_url_still_yields_relative_paths(share_tree):
    manifest = Manifest.build([share_tree / "hello.txt"])
    payload = manifest.as_dict()
    entry = payload["files"][0]
    assert entry["url"].startswith("/f/")
    assert entry["download_url"].startswith("/download/")


# ── symlinks must not publish anything outside the share ────────────────────
def test_a_symlink_pointing_outside_the_share_is_not_listed(tmp_path):
    """Sharing ~/Documents with one stray link must not expose its target."""
    shared = tmp_path / "Documents"
    shared.mkdir()
    (shared / "notes.txt").write_text("inside", encoding="utf-8")

    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    make_symlink(shared / "innocent.txt", secret)

    from local_file_sharer.transfer.manifest import Manifest

    names = {entry.name for entry in Manifest.build([shared]).iter_files()}
    assert "notes.txt" in names
    assert "innocent.txt" not in names, "a link out of the share must not be published"


def test_a_symlink_inside_the_share_is_still_listed(tmp_path):
    """Sensible layouts (a link to a sibling file) keep working."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "real.txt").write_text("data", encoding="utf-8")
    make_symlink(shared / "alias.txt", shared / "real.txt")

    from local_file_sharer.transfer.manifest import Manifest

    names = {entry.name for entry in Manifest.build([shared]).iter_files()}
    assert {"real.txt", "alias.txt"} <= names


def test_a_dangling_symlink_does_not_break_the_manifest(tmp_path):
    from local_file_sharer.transfer.manifest import Manifest

    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("fine", encoding="utf-8")
    make_symlink(shared / "gone.txt", tmp_path / "does-not-exist")

    names = {entry.name for entry in Manifest.build([shared]).iter_files()}
    assert "ok.txt" in names
    assert "gone.txt" not in names
