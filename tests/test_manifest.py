"""Manifest → JSON: the schema the web UIs and the torrent builder depend on."""

from __future__ import annotations

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
