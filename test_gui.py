import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import gui
from csv_parser import Track


class OriginAllowedTests(unittest.TestCase):
    def test_github_pages_and_localhost(self) -> None:
        self.assertTrue(gui.origin_allowed("https://kamil.github.io"))
        self.assertTrue(gui.origin_allowed("http://127.0.0.1:8765"))
        self.assertFalse(gui.origin_allowed("https://evil.example"))

    def test_saves_mp3_via_browser_download(self) -> None:
        self.assertEqual(gui.AUDIO_FORMAT, "mp3")
        self.assertEqual(gui.DEFAULT_OUTPUT, gui.APP_DIR / ".cache")
        self.assertNotEqual(gui.DEFAULT_OUTPUT, Path.home() / "Downloads")

    def test_local_bind_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gui.is_cloud())
            self.assertEqual(gui.bind_address(), ("127.0.0.1", 8765))

    def test_railway_binds_port_on_all_interfaces(self) -> None:
        with patch.dict(
            os.environ,
            {"RAILWAY_ENVIRONMENT": "production", "PORT": "8080"},
            clear=True,
        ):
            self.assertTrue(gui.is_cloud())
            self.assertEqual(gui.bind_address(), ("0.0.0.0", 8080))


class GuiApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        gui.STATE.reset()

    def _get(self, path: str):
        with urlopen(self.base + path) as response:
            return response.status, response.read()

    def _post(self, path: str, payload: dict):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_index_page(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Spotidrop", body)
        self.assertNotIn(b"YouTube Music Downloader", body)
        self.assertNotIn(b'id="engine"', body)
        self.assertNotIn("Silnik".encode(), body)
        self.assertIn(b"Avicii", body)
        self.assertIn(b"Wake Me Up", body)
        self.assertNotIn(b"Kobosil", body)
        self.assertIn(b"API_BASE", body)
        self.assertIn(b"127.0.0.1:8765", body)
        self.assertIn(b'class="cover"', body)
        self.assertIn(b"const src = remote || local", body)
        self.assertIn(b"data-dl", body)
        self.assertNotIn(b"CSV ze Spotify", body)
        self.assertIn(b"/track/", body)
        self.assertIn("Pobrane".encode(), body)
        self.assertIn(b"/api/file/", body)
        self.assertIn(b"offerBrowserDownload", body)
        self.assertIn(b"waiting.add", body)
        self.assertNotIn(b">Format<", body)
        self.assertNotIn(b">Folder<", body)
        self.assertNotIn("Otwórz folder".encode(), body)

    def test_cors_preflight_from_github_pages(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request(
            "OPTIONS",
            "/api/state",
            headers={
                "Origin": "https://kamil.github.io",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        response = conn.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "https://kamil.github.io")
        self.assertEqual(response.getheader("Access-Control-Allow-Private-Network"), "true")
        conn.close()

    def test_start_without_tracks(self) -> None:
        status, data = self._post("/api/start", {"mode": "link"})
        self.assertEqual(status, 400)
        self.assertIn("link", data["error"].lower())

    def test_start_requires_selection(self) -> None:
        gui.STATE.tracks = [Track(title="Get Lucky", artist="Daft Punk")]
        gui.STATE.statuses = ["—"]
        status, data = self._post("/api/start", {"mode": "link", "indices": []})
        self.assertEqual(status, 400)
        self.assertIn("Zaznacz", data["error"])

    def test_spotify_invalid_url(self) -> None:
        status, data = self._post("/api/spotify", {"url": "https://example.com"})
        self.assertEqual(status, 400)
        self.assertIn("Spotify", data["error"])

    def test_start_single_requires_fields(self) -> None:
        status, data = self._post("/api/start", {"mode": "single", "artist": "X"})
        self.assertEqual(status, 400)
        self.assertIn("artyst", data["error"].lower())

    def test_file_missing_returns_404(self) -> None:
        try:
            urlopen(self.base + "/api/file/0")
            self.fail("expected HTTPError")
        except HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_file_endpoint_serves_mp3_as_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Daft Punk - Get Lucky.mp3"
            audio.write_bytes(b"ID3fake-mp3")
            gui.STATE.output_dir = str(folder)
            gui.STATE.audio_format = "mp3"
            gui.STATE.tracks = [Track(title="Get Lucky", artist="Daft Punk")]
            gui.STATE.statuses = ["OK"]
            request = Request(self.base + "/api/file/0")
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), "application/octet-stream")
                disposition = response.getheader("Content-Disposition") or ""
                self.assertIn("attachment", disposition)
                self.assertIn("Get Lucky.mp3", disposition)
                self.assertEqual(response.read(), b"ID3fake-mp3")

    def test_cover_missing_returns_404(self) -> None:
        try:
            urlopen(self.base + "/api/cover/0")
            self.fail("expected HTTPError")
        except HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_cover_endpoint_serves_embedded_art(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Daft Punk - Get Lucky.mp3"
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono:d=0.1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=32x32:d=0.1",
                    "-shortest",
                    "-map",
                    "0:a",
                    "-map",
                    "1:v",
                    "-c:a",
                    "libmp3lame",
                    "-c:v",
                    "mjpeg",
                    "-disposition:v",
                    "attached_pic",
                    "-id3v2_version",
                    "3",
                    str(audio),
                ],
                capture_output=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0 or not audio.exists():
                self.skipTest("ffmpeg cannot create a test MP3 with cover")
            gui.STATE.output_dir = str(folder)
            gui.STATE.audio_format = "mp3"
            gui.STATE.tracks = [Track(title="Get Lucky", artist="Daft Punk")]
            gui.STATE.statuses = ["OK"]
            status, body = self._get("/api/cover/0")
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 50)
