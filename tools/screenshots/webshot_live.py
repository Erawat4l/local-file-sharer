#!/usr/bin/env python3
"""Capture ``docs/assets/web-*.png`` from the *running* pages, not a drawing.

The README's three web screenshots used to be hand-authored SVG: ``webgen.py``
re-drew the pages with the design tokens so the files would be deterministic in
a diff. Deterministic, yes -- and wrong. Nothing compared the drawing to the
markup, so the pictures drifted: they showed a "Filter files…" box the
downloader never had, two solid buttons where the page draws one, and a header
line that existed in no template. The README advertised a product that did not
exist.

This script removes the middleman. It boots the real ``ShareServer`` on a
loopback port, points headless Chromium at it over CDP, and saves what a person
would actually see. Whatever the HTML and CSS say is what the PNG says, because
the PNG *is* the page.

Requirements (maintainer machine only, never CI):

* a Chromium/Chrome binary, passed as ``--chrome`` or found on ``PATH``
* ``websocket-client``  (``pip install websocket-client``)
* a demo tree to share, passed as ``--root``

    PYTHONPATH=src python tools/screenshots/webshot_live.py \\
        --chrome /tmp/chrome/chromium --root /tmp/demo-tree

Chromium is launched with ``LD_LIBRARY_PATH`` inherited, so an
AppImage-style build that needs its bundled ``libnspr4``/``libnss3`` works if
the caller sets that up.

``web-text.txt`` is written from the live DOM (``innerText`` of ``body`` plus
every ``aria-label`` and ``title``), so the copy a test holds against the
templates is copy the browser was actually given.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "docs" / "assets"

#: 1440 wide, times two for a retina-crisp PNG. The player is taller than the
#: two share pages: its stage is 100vh, so a 900px window pushes the
#: "Streaming with HTTP Range" line exactly one pixel below the fold and the
#: shot silently loses it. Give each page the height it actually needs.
VIEWPORT = {"web-download": (1440, 900), "web-server": (1440, 900), "web-player": (1440, 1040)}
SCALE = 2


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ── Chromium over CDP ───────────────────────────────────────────────────────
class Browser:
    """Just enough CDP to navigate a page and take a picture of it."""

    def __init__(self, binary: str, port: int | None = None) -> None:
        # A free port every time. Reusing the default 9222 silently attached to
        # whatever Chromium was already listening there, and that stale browser
        # handed back a page whose QR had never painted -- a white square in the
        # README that no amount of waiting in this process could fix.
        self.port = port or free_port()
        self.process = subprocess.Popen(  # noqa: S603
            [
                binary,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--remote-debugging-port={self.port}",
                "--remote-allow-origins=*",
                # A fresh profile per run: sharing one makes the second launch
                # hand off to the first instance and exit immediately, leaving
                # this process waiting on a port nothing will ever open.
                f"--user-data-dir={tempfile.mkdtemp(prefix='lfs-webshot-')}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._ws: Any = None
        self._id = 0
        self._wait_for_port()

    def _wait_for_port(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.4):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"Chromium never opened port {self.port}")

    def _connect(self) -> None:
        import websocket  # noqa: PLC0415 - optional maintainer dependency

        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))  # noqa: S310
        page = next(tab for tab in tabs if tab["type"] == "page")
        self._ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        if self._ws is None:
            self._connect()
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") == self._id:
                return message

    def capture(
        self,
        url: str,
        out: Path,
        *,
        size: tuple[int, int],
        settle: float = 2.0,
        after: str | None = None,
    ) -> str:
        """Save a PNG of ``url`` and return the page's visible text.

        ``after`` is JavaScript run once the page has settled, for states a URL
        cannot express -- the player with its controls faded out, for instance.
        """
        width, height = size
        self._call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=SCALE,
            mobile=False,
        )
        self._call("Page.enable")
        self._call("Page.navigate", url=url)
        # The QR is a second request the page makes after load: a fixed sleep
        # once captured a white square where the code should be. Wait for the
        # thing itself -- an <img src> that is no longer the empty placeholder --
        # and fall back to the timeout on pages that have no QR at all.
        deadline = time.time() + settle * 3
        while time.time() < deadline:
            ready = self._call(
                "Runtime.evaluate",
                expression=(
                    # The placeholder is a ~90-character empty SVG; the real
                    # code is a 13kB data URL. Length separates them where a
                    # substring test did not: both are SVG data URLs.
                    "(() => { const qr = document.querySelector('#qr');"
                    " if (!qr) return true;"
                    " return qr.complete && qr.naturalWidth > 0"
                    " && String(qr.src).length > 2000; })()"
                ),
                returnByValue=True,
            )
            if ready["result"]["result"]["value"] is True:
                break
            time.sleep(0.15)
        time.sleep(0.5)
        if after:
            self._call("Runtime.evaluate", expression=after, awaitPromise=True)
            time.sleep(0.4)
        shot = self._call("Page.captureScreenshot", format="png")
        out.write_bytes(base64.b64decode(shot["result"]["data"]))

        text = self._call("Runtime.evaluate", expression=_RECORD_TEXT_JS, returnByValue=True)
        return text["result"]["result"]["value"]

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.process.kill()


#: Read one label per *element*, not one per rendered line.
#:
#: ``body.innerText`` breaks at every block box, and a chip is a flex container,
#: so "Mode <b>download</b>" came back as two lines and the recorded copy no
#: longer matched the page. Each candidate is cloned, its descendants forced
#: inline, and its ``innerText`` read back -- that keeps CSS ``text-transform``
#: (so "DOWNLOAD STATION" is still recorded as uppercase) while joining the runs
#: a person actually sees as one label.
_RECORD_TEXT_JS = """
JSON.stringify((() => {
  const sel = 'h1,h2,h3,h4,h5,h6,p,button,a,label,option,li,th,td,input,small,'
            + '.brand span,aside span,.row span,.topline span,.chip,.btn,.k,.v,.muted,.mono,'
            + '.lead,.qr-caption,.understage span,footer span';
  // Read each element in place: innerText applies CSS text-transform, and only
  // the live tree still has the ancestor styles that supply it (a detached
  // clone of .brand > small reports "Download Station", not "DOWNLOAD STATION").
  // Elements whose own text repeats their parent's are skipped, so the copy is
  // one label per *visible* run rather than one per wrapper.
  const labels = [];
  const parentText = new Map();
  const textOf = (el) => {
    // An empty input has no innerText; the label a person reads is its
    // placeholder ("Search name or type — try mp4").
    if (el.tagName === 'INPUT') return (el.placeholder || el.value || '').trim();
    return el.innerText.replace(/\\s+/g, ' ').trim();
  };
  document.querySelectorAll(sel).forEach(el => {
    const text = textOf(el);
    if (!text || text.length > 120) return;
    const parent = el.parentElement;
    if (parent && parentText.has(parent) && parentText.get(parent) === text) return;
    parentText.set(el, text);
    // A visually-hidden uppercase label rides along with the value beside it
    // ("FILES9"); the visible .k/.v pair is recorded separately.
    // .stat is a wrapper around the label and its value, both recorded on
    // their own as ".k" and ".v"; skip the composite so the record does not
    // carry "FILES 9" beside "FILES" and "9".
    if (el.classList.contains('stat')) return;
    if (!labels.includes(text)) labels.push(text);
  });
  return {
    labels,
    aria: [...document.querySelectorAll('[aria-label]')]
      .map(el => el.getAttribute('aria-label')),
    titles: [...document.querySelectorAll('[title]')]
      .map(el => el.getAttribute('title')),
  };
})())
"""


# ── the pages ───────────────────────────────────────────────────────────────
def first_player_path(base: str) -> str:
    """``/s/<id>`` for the first playable file the share page links.

    The manifest lists the roots a person picked, not the files inside them, so
    the page itself is the honest index. Its cards link every video and every
    audio file at ``/s/<id>``.
    """
    import re  # noqa: PLC0415

    seen: set[str] = set()
    # Breadth-first, one folder deep enough for the usual "share a folder of
    # clips" case: the files may sit under a subfolder, and the front page only
    # links the folders.
    queue = ["/"]
    while queue:
        page_path = queue.pop(0)
        if page_path in seen:
            continue
        seen.add(page_path)
        page = urllib.request.urlopen(base + page_path, timeout=10).read().decode("utf-8", "replace")  # noqa: S310
        links = re.findall(r'href="(/s/[^"]+)"', page)
        if links:
            return links[0]
        queue.extend(re.findall(r'href="(/browse/[^"]+)"', page))
        if len(seen) > 25:  # a sane ceiling; no share needs more
            break
    raise RuntimeError(f"no playable file in {base}/ -- point --root at a folder with a video")


def serve(root: Path, mode: str, peer: str) -> tuple[Any, int]:
    """Boot a real server for one mode and return it with its port."""
    sys.path.insert(0, str(REPO / "src"))
    from local_file_sharer.transfer.manifest import Manifest  # noqa: PLC0415
    from local_file_sharer.transfer.server import ShareServer  # noqa: PLC0415

    port = free_port()
    server = ShareServer(Manifest.build([root], mode=mode), host="127.0.0.1", port=port)
    server.start()
    # A fixed, obviously-fake address: the QR and the copy-one-liner both read
    # it, and a link-local address from this sandbox would leak into the PNG.
    server.set_base_url(f"http://192.168.1.42:{port}")
    server._server.context.peer_name = peer  # noqa: SLF001 - a shot fixture
    return server, port


#: Page states a URL alone cannot express, run once the page has settled.
#:
#: Browse Folder's primary action is disabled until something is ticked, so a
#: plain shot shows only grey buttons -- a picture of a page in a state nobody
#: uses it in. Two ticked rows show the real control, and the count in its
#: label, the way arriving with a choice makes the page look.
AFTER_LOAD = {
    "web-server": (
        "(() => {"
        " const rows = [...document.querySelectorAll('.row .pick')].slice(0, 2);"
        " rows.forEach(cb => cb.click());"
        " return rows.length; })()"
    ),
}


#: What each page is, and how it is opened.
PAGES = (("web-download", "download"), ("web-server", "serve"), ("web-player", "stream"))


def page_urls(root: Path, peer: str) -> dict[str, tuple[Any, str]]:
    """Boot one server per mode and return ``{name: (server, url)}``."""
    out: dict[str, tuple[Any, str]] = {}
    for name, mode in PAGES:
        server, port = serve(root, mode, peer)
        base = f"http://127.0.0.1:{port}"
        url = base + (first_player_path(base) if mode == "stream" else "/")
        out[name] = (server, url)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", required=True, help="Chromium/Chrome binary")
    parser.add_argument("--root", required=True, type=Path, help="folder to share")
    parser.add_argument("--peer", default="my-laptop", help="device name in the hero")
    parser.add_argument("--keep", action="store_true", help="keep the PNGs in /tmp too")
    args = parser.parse_args(argv)

    if not (args.root / "").exists():
        print(f"no such folder: {args.root}", file=sys.stderr)
        return 2
    # The QR is drawn server-side by the optional-ish `qrcode` package. Without
    # it the page keeps its placeholder by design, and the capture would ship a
    # blank white square as if that were the product. Fail instead of shooting.
    try:
        import qrcode  # noqa: F401, PLC0415
    except ImportError:
        print(
            "the `qrcode` package is missing, so /api/qr answers 500 and the "
            "hero shot would be a blank square.\n"
            "  pip install qrcode    (it is a normal dependency of this project)",
            file=sys.stderr,
        )
        return 2

    ASSETS.mkdir(parents=True, exist_ok=True)
    browser = Browser(args.chrome)
    servers: list[Any] = []
    captured: list[tuple[str, str]] = []
    try:
        plans = (("web-download", "download"), ("web-server", "serve"), ("web-player", "stream"))
        for name, mode in plans:
            server, port = serve(args.root, mode, args.peer)
            servers.append(server)
            base = f"http://127.0.0.1:{port}"
            url = base + "/"
            if mode == "stream":
                url = base + first_player_path(base)
            raw = browser.capture(
                url,
                ASSETS / f"{name}.png",
                size=VIEWPORT[name],
                after=AFTER_LOAD.get(name),
            )
            payload = json.loads(raw)
            captured.append((name, payload))
            print(f"{name}.png  ({url})")
    finally:
        browser.close()
        for server in servers:
            server.stop()

    # One label per line, in page order, exactly what the browser rendered.
    lines: list[str] = []
    for _name, payload in captured:
        for chunk in (*payload["labels"], *payload["aria"], *payload["titles"]):
            for line in str(chunk).splitlines():
                line = line.strip()
                # Exact duplicates only. A case-insensitive merge looked tidy
                # but ate real labels: Browse Folder's "TRANSFERRED" and the
                # player's "transferred" are different words on different
                # pages, and collapsing them lost the player's stat row.
                if line and line not in lines:
                    lines.append(line)
    (ASSETS / "web-text.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"web-text.txt: {len(lines)} labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
