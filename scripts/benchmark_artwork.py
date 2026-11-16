"""BENCH-03: a loopback HTTPS server that answers provider artwork URLs.

Fixture items carry provider-shaped image URLs (``app.benchmark_data``). The
browser maps those hosts to this server with ``--host-resolver-rules``, so
artwork travels through Chromium's real network stack: DevTools network
throttling and the HTTP cache apply, which they do not for responses faked with
``route.fulfill``. Images are generated locally (never downloaded) at the pixel
size the URL asks for, as baseline JPEGs with photo-like detail so the byte
weight and decode cost resemble real posters.
"""

import base64
import hashlib
import io
import re
import ssl
import subprocess
import tempfile
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOSTS = (
    "image.tmdb.org",
    "images.igdb.com",
    "covers.openlibrary.org",
    "cdn.myanimelist.net",
    "coverartarchive.org",
    "is1-ssl.mzstatic.com",
)
VARIANTS = 12

# (pattern on the URL path, width, height); first match wins.
_SIZES = (
    (re.compile(r"/t/p/w(\d+)/[^/]*still"), None, 9 / 16),
    (re.compile(r"/t/p/original/[^/]*still"), 1920, 9 / 16),
    (re.compile(r"/t/p/w(1280|300)/"), None, 9 / 16),
    (re.compile(r"/t/p/w(\d+)/"), None, 1.5),
    (re.compile(r"/t/p/original/"), 2000, 1.5),
    (re.compile(r"/t_cover_big_2x/"), 528, 704 / 528),
    (re.compile(r"/t_cover_big/"), 264, 352 / 264),
    (re.compile(r"/t_original/"), 1200, 1600 / 1200),
    (re.compile(r"-L\.jpg$"), 400, 1.5),
    (re.compile(r"-M\.jpg$"), 180, 1.5),
    (re.compile(r"/images/manga/"), 225, 350 / 225),
    (re.compile(r"-(\d+)\.jpg$"), None, 1.0),
    (re.compile(r"/(\d+)x\1bb\.jpg$"), None, 1.0),
)


def image_size(path):
    """Return the pixel size a provider URL path stands for."""
    for pattern, fixed, ratio in _SIZES:
        match = pattern.search(path)
        if match:
            width = fixed or int(match.group(1))
            return width, round(width * ratio)
    return 300, 450


@lru_cache(maxsize=256)
def jpeg(width, height, variant):
    """Generate one deterministic photo-like JPEG (gradient, shapes, grain)."""
    from PIL import Image, ImageDraw, ImageFilter

    seed = hashlib.sha256(f"{width}x{height}:{variant}".encode()).digest()
    top, bottom = seed[:3], seed[3:6]
    image = Image.linear_gradient("L").resize((width, height))
    image = Image.merge(
        "RGB",
        [
            image.point(lambda v, a=a, b=b: a + (b - a) * v // 255)
            for a, b in zip(top, bottom, strict=True)
        ],
    )
    draw = ImageDraw.Draw(image)
    for index in range(14):
        box_seed = seed[(index * 2) % 28 : (index * 2) % 28 + 4]
        x, y = box_seed[0] * width // 255, box_seed[1] * height // 255
        radius = (box_seed[2] % 40 + 10) * width // 160
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=tuple(seed[(index + n) % 32] for n in range(3)),
        )
    image = image.filter(ImageFilter.GaussianBlur(width / 120))
    grain = Image.effect_noise((width, height), 22).convert("RGB")
    image = Image.blend(image, grain, 0.18)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    served = None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        width, height = image_size(path)
        variant = int(hashlib.sha256(path.encode()).hexdigest(), 16) % VARIANTS
        body = jpeg(width, height, variant)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=31536000")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        with _LOCK:
            _Handler.served.append(len(body))

    def log_message(self, *args):
        """Stay quiet."""


_LOCK = threading.Lock()


class ArtworkServer:
    """Context manager: a threaded loopback HTTPS artwork server."""

    def __enter__(self):
        """Start the server with a throwaway self-signed certificate."""
        self._dir = tempfile.TemporaryDirectory()
        cert, key = Path(self._dir.name, "cert.pem"), Path(self._dir.name, "key.pem")
        subprocess.run(  # noqa: S603 -- fixed openssl invocation, no shell
            [  # noqa: S607 -- system openssl
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "2",
                "-subj",
                "/CN=floppy-bench-artwork",
            ],
            check=True,
            capture_output=True,
        )
        public = subprocess.run(  # noqa: S603 -- fixed openssl invocation
            ["openssl", "x509", "-pubkey", "-noout", "-in", str(cert)],  # noqa: S607
            check=True,
            capture_output=True,
        ).stdout
        spki = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "der"],  # noqa: S607
            input=public,
            check=True,
            capture_output=True,
        ).stdout
        self.spki_pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
        _Handler.served = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def resolver_rules(self):
        """Chromium flag value sending every artwork host to this server."""
        port = self.server.server_port
        return ", ".join(f"MAP {host} 127.0.0.1:{port}" for host in HOSTS)

    @property
    def browser_args(self):
        """Chromium flags: route artwork hosts here and trust only this certificate.

        Pinning the key (rather than ignoring certificate errors) matters: Chromium
        does not cache responses from an origin whose certificate it rejected, so
        warm visits would re-download every image.
        """
        return [
            f"--ignore-certificate-errors-spki-list={self.spki_pin}",
        ]

    @property
    def served_bytes(self):
        """Bytes served so far (all requests)."""
        with _LOCK:
            return sum(_Handler.served)

    def __exit__(self, *exc):
        """Stop the server and delete the certificate."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self._dir.cleanup()
