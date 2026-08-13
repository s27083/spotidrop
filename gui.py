#!/usr/bin/env python3
"""GUI w przeglądarce — lokalny serwer na localhost."""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from csv_parser import Track
from downloader import (
    DownloadFailed,
    download_track,
    ensure_ffmpeg,
    existing_file,
    extract_cover_bytes,
    install_cookies_from_env,
)
from spotify import SpotifyFetchError, fetch_spotify_tracks

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
CACHE_DIR = APP_DIR / ".cache"
DEFAULT_OUTPUT = CACHE_DIR
AUDIO_FORMAT = "mp3"
HOST = "127.0.0.1"
PORT = 8765
PAGES_ORIGIN = os.environ.get("PAGES_ORIGIN", "").rstrip("/")


def is_cloud() -> bool:
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL"))


def bind_address() -> tuple[str, int]:
    port = int(os.environ.get("PORT", str(PORT)))
    host = os.environ.get("HOST") or ("0.0.0.0" if is_cloud() else HOST)
    return host, port


COVER_ROUTE = re.compile(r"^/api/cover/(\d+)$")
FILE_ROUTE = re.compile(r"^/api/file/(\d+)$")


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.busy = False
            self.stop_event = threading.Event()
            self.tracks: list[Track] = []
            self.statuses: list[str] = []
            self.log_lines: list[str] = []
            self.csv_name = ""
            self.output_dir = str(DEFAULT_OUTPUT)
            self.audio_format = AUDIO_FORMAT
            self.status = "Gotowe"
            self.current = 0
            self.total = 0
            self.ok = 0
            self.failed = 0
            self.ffmpeg_error = self._ffmpeg_error()

    @staticmethod
    def _ffmpeg_error() -> str:
        try:
            ensure_ffmpeg()
        except RuntimeError as exc:
            return str(exc)
        return ""

    def snapshot(self, *, light: bool = False) -> dict:
        with self.lock:
            payload = {
                "busy": self.busy,
                "ffmpeg_error": self.ffmpeg_error,
                "output_dir": self.output_dir,
                "audio_format": AUDIO_FORMAT,
                "status": self.status,
                "current": self.current,
                "total": self.total,
                "ok": self.ok,
                "failed": self.failed,
                "csv_name": self.csv_name,
                "log": self.log_lines[-200:],
            }
            if light:
                payload["statuses"] = list(self.statuses)
                return payload
            payload["tracks"] = [
                {
                    "artist": track.artist,
                    "title": track.title,
                    "album": track.album,
                    "cover_url": track.cover_url,
                    "status": self.statuses[index] if index < len(self.statuses) else "—",
                }
                for index, track in enumerate(self.tracks)
            ]
            return payload

    def log(self, message: str) -> None:
        with self.lock:
            self.log_lines.append(message)
            if len(self.log_lines) > 400:
                self.log_lines = self.log_lines[-300:]


STATE = AppState()


def origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if PAGES_ORIGIN and origin.rstrip("/") == PAGES_ORIGIN:
        return True
    host = urlparse(origin).hostname or ""
    if host in {"127.0.0.1", "localhost"}:
        return True
    return host.endswith(".github.io")


def _json_bytes(payload: dict, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _set_tracks(tracks: list[Track], csv_name: str = "") -> None:
    output = Path(STATE.output_dir)
    audio_format = STATE.audio_format
    statuses = []
    for track in tracks:
        statuses.append("OK" if existing_file(output, track, audio_format) else "—")
    with STATE.lock:
        STATE.tracks = tracks
        STATE.statuses = statuses
        STATE.csv_name = csv_name
        STATE.current = 0
        STATE.total = len(tracks)
        STATE.ok = 0
        STATE.failed = 0
        STATE.status = f"Wczytano {len(tracks)} utworów"


def _download_loop(
    tracks: list[Track],
    indices: list[int],
    output_dir: Path,
    audio_format: str,
) -> None:
    ok = 0
    failed = 0
    total = len(indices)
    for step, index in enumerate(indices):
        if STATE.stop_event.is_set():
            STATE.log("Przerwane.")
            break
        track = tracks[index]
        with STATE.lock:
            STATE.statuses[index] = "szukam"
            STATE.current = step
            STATE.status = f"{step + 1} / {total}"
        STATE.log(f"[{step + 1}/{total}] Szukam: {track.display_name()}")
        try:
            path = download_track(
                track,
                output_dir=output_dir,
                audio_format=audio_format,
                log=STATE.log,
            )
            with STATE.lock:
                STATE.statuses[index] = "OK"
            STATE.log(f"OK: {path.name}")
            ok += 1
        except DownloadFailed as exc:
            with STATE.lock:
                STATE.statuses[index] = "błąd"
            STATE.log(f"FAIL: {exc}")
            failed += 1
        with STATE.lock:
            STATE.current = step + 1
            STATE.ok = ok
            STATE.failed = failed

    if STATE.stop_event.is_set() and ok + failed < total:
        remaining = total - ok - failed
        STATE.log(f"Pominięto {remaining} utworów.")
    summary = f"Gotowe. Pobrane: {ok}, błędy: {failed}"
    with STATE.lock:
        STATE.busy = False
        STATE.status = summary
        STATE.ok = ok
        STATE.failed = failed
    STATE.log(summary)


def handle_spotify(body: dict) -> tuple[int, dict]:
    url = (body.get("url") or "").strip()
    if not url:
        return 400, {"error": "Wklej link do playlisty, albumu albo utworu Spotify."}
    try:
        name, tracks = fetch_spotify_tracks(url)
    except SpotifyFetchError as exc:
        return 400, {"error": str(exc)}
    _set_tracks(tracks, csv_name=name)
    STATE.log(f"Wczytano {len(tracks)} utworów z „{name}”")
    return 200, STATE.snapshot()


def handle_start(body: dict) -> tuple[int, dict]:
    with STATE.lock:
        if STATE.busy:
            return 409, {"error": "Pobieranie już trwa."}

    mode = body.get("mode") or "link"
    output_dir = DEFAULT_OUTPUT
    audio_format = AUDIO_FORMAT

    raw_indices = body.get("indices")
    if mode == "single" and raw_indices is None:
        artist = (body.get("artist") or "").strip()
        title = (body.get("title") or "").strip()
        if not artist or not title:
            return 400, {"error": "Podaj artystę i tytuł."}
        tracks = [Track(title=title, artist=artist)]
        _set_tracks(tracks)
        indices = [0]
    else:
        with STATE.lock:
            tracks = list(STATE.tracks)
        if not tracks:
            return 400, {"error": "Najpierw wczytaj link ze Spotify albo podaj artystę i tytuł."}
        if raw_indices is None:
            indices = list(range(len(tracks)))
        else:
            indices = [
                index
                for index in raw_indices
                if isinstance(index, int) and 0 <= index < len(tracks)
            ]
        if not indices:
            return 400, {"error": "Zaznacz przynajmniej jeden utwór."}

    try:
        ensure_ffmpeg()
    except RuntimeError as exc:
        return 400, {"error": str(exc)}

    output_dir.mkdir(parents=True, exist_ok=True)
    with STATE.lock:
        STATE.busy = True
        STATE.stop_event = threading.Event()
        STATE.output_dir = str(output_dir)
        STATE.audio_format = audio_format
        STATE.current = 0
        STATE.total = len(indices)
        STATE.ok = 0
        STATE.failed = 0
        STATE.status = f"0 / {len(indices)}"
        for index in indices:
            STATE.statuses[index] = "—"

    worker = threading.Thread(
        target=_download_loop,
        args=(tracks, indices, output_dir, audio_format),
        daemon=True,
    )
    worker.start()
    return 200, STATE.snapshot()


def handle_stop() -> tuple[int, dict]:
    STATE.stop_event.set()
    STATE.log("Zatrzymuję po bieżącym utworze…")
    return 200, {"ok": True}


def handle_cover(index: int) -> tuple[int, bytes, str]:
    with STATE.lock:
        if index < 0 or index >= len(STATE.tracks):
            return 404, b"Not found", "text/plain"
        track = STATE.tracks[index]
        output_dir = Path(STATE.output_dir)
        audio_format = STATE.audio_format
    audio = existing_file(output_dir, track, audio_format)
    if not audio:
        return 404, b"Not found", "text/plain"
    data = extract_cover_bytes(audio)
    if not data:
        return 404, b"Not found", "text/plain"
    return 200, data, "image/jpeg"


def _content_disposition(filename: str) -> str:
    cleaned = filename.replace("\r", " ").replace("\n", " ").replace('"', "'")
    ascii_name = cleaned.encode("ascii", "replace").decode("ascii")
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(cleaned, safe='')}"
    )


def handle_file(index: int) -> tuple[int, bytes, str, dict[str, str] | None]:
    with STATE.lock:
        if index < 0 or index >= len(STATE.tracks):
            return 404, b"Not found", "text/plain", None
        track = STATE.tracks[index]
        output_dir = Path(STATE.output_dir)
        audio_format = STATE.audio_format
    audio = existing_file(output_dir, track, audio_format)
    if not audio:
        return 404, b"Not found", "text/plain", None
    try:
        data = audio.read_bytes()
    except OSError:
        return 404, b"Not found", "text/plain", None
    return (
        200,
        data,
        "application/octet-stream",
        {"Content-Disposition": _content_disposition(audio.name)},
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin") or ""
        if origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Access-Control-Request-Private-Network",
            )
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 20 * 1024 * 1024:
            raise ValueError("Plik jest za duży.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/state":
            query = parse_qs(parsed.query)
            light = (query.get("light") or [""])[0] in {"1", "true"}
            status, body = _json_bytes(STATE.snapshot(light=light))
            self._send(status, body, "application/json; charset=utf-8")
            return
        cover_match = COVER_ROUTE.match(path)
        if cover_match:
            status, body, content_type = handle_cover(int(cover_match.group(1)))
            self._send(status, body, content_type)
            return
        file_match = FILE_ROUTE.match(path)
        if file_match:
            status, body, content_type, extra = handle_file(int(file_match.group(1)))
            self._send(status, body, content_type, extra)
            return
        if path in {"/", "/index.html"}:
            page = WEB_DIR / "index.html"
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            status, payload = _json_bytes({"error": str(exc)}, 400)
            self._send(status, payload, "application/json; charset=utf-8")
            return

        if path == "/api/spotify":
            code, data = handle_spotify(body)
        elif path == "/api/start":
            code, data = handle_start(body)
        elif path == "/api/stop":
            code, data = handle_stop()
        else:
            code, data = 404, {"error": "Not found"}

        status, payload = _json_bytes(data, code)
        self._send(status, payload, "application/json; charset=utf-8")


def run_gui(open_browser: bool = True) -> None:
    cookies = install_cookies_from_env()
    print(f"YouTube cookies: {'tak' if cookies else 'brak'}", flush=True)
    host, port = bind_address()
    httpd = None
    chosen = port
    ports = [port] if is_cloud() else list(range(port, port + 15))
    for candidate in ports:
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            chosen = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("Nie mogę uruchomić serwera GUI — zajęte porty.")

    url = f"http://{host}:{chosen}"
    print(f"GUI: {url}", flush=True)
    if open_browser and not is_cloud():
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nZamykam GUI.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_gui()
