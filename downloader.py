"""Wyszukuje i pobiera audio z YouTube Music / YouTube przez yt-dlp."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from csv_parser import Track
from match import candidate_label, pick_best_candidate

ProgressCallback = Callable[[str], None]
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
COVER_MAX_BYTES = 5 * 1024 * 1024
COVER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class DownloadFailed(Exception):
    pass


APP_DIR = Path(__file__).resolve().parent
DEFAULT_COOKIES = APP_DIR / "cookies.txt"
BOT_HINT = (
    "YouTube blokuje to IP (na Railway tak jest prawie zawsze). "
    "Wyeksportuj cookies z Chrome (zalogowany YouTube) do cookies.txt "
    "i wklej zawartość jako zmienną YTDLP_COOKIES_CONTENT."
)


def _sanitize_filename(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden else ch for ch in name)
    return " ".join(cleaned.split()).strip(" .") or "track"


def track_stem(track: Track) -> str:
    if track.artist and track.title:
        return f"{_sanitize_filename(track.artist)} - {_sanitize_filename(track.title)}"
    return _sanitize_filename(track.title or track.artist)


def _output_template(output_dir: Path, track: Track) -> str:
    return str(output_dir / f"{track_stem(track)}.%(ext)s")


def cover_path(output_dir: Path, track: Track) -> Path:
    return output_dir / f"{track_stem(track)}.jpg"


def existing_file(output_dir: Path, track: Track, audio_format: str) -> Path | None:
    candidate = output_dir / f"{track_stem(track)}.{audio_format}"
    return candidate if candidate.exists() else None


def _files_for_track(output_dir: Path, track: Track):
    if not output_dir.is_dir():
        return
    stem = track_stem(track)
    for path in output_dir.iterdir():
        if path.is_file() and path.stem == stem:
            yield path


def existing_cover(output_dir: Path, track: Track) -> Path | None:
    for path in _files_for_track(output_dir, track):
        if path.suffix.lower() in COVER_EXTENSIONS and path.stat().st_size > 0:
            return path
    return None


def cookies_path() -> Path:
    return Path(os.environ.get("YTDLP_COOKIES", str(DEFAULT_COOKIES)))


def install_cookies_from_env() -> Path | None:
    raw = os.environ.get("YTDLP_COOKIES_CONTENT", "").strip()
    path = cookies_path()
    if raw:
        path.write_text(raw if raw.endswith("\n") else f"{raw}\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _auth_options() -> dict:
    options: dict = {
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv", "web"],
            }
        }
    }
    path = install_cookies_from_env()
    if path is not None:
        options["cookiefile"] = str(path)
        return options
    browser = (os.environ.get("YTDLP_BROWSER") or "").strip().lower()
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    return options


def _is_bot_block(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "not a bot" in text or "cookies-from-browser" in text or "sign in to confirm" in text


def _ydl_options(
    output_dir: Path,
    track: Track,
    audio_format: str,
) -> dict:
    options = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "overwrites": False,
        "noplaylist": True,
        "writethumbnail": False,
        "writeinfojson": False,
        "writesubtitles": False,
        "outtmpl": _output_template(output_dir, track),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "postprocessor_args": {
            "FFmpegExtractAudio": ["-ar", "44100"],
        },
    }
    options.update(_auth_options())
    return options


def _search_queries(track: Track) -> list[str]:
    query = track.search_query()
    queries = [f"ytsearch8:{query}", f"https://music.youtube.com/search?q={quote_plus(query)}"]
    alt = " ".join(part for part in (track.title, track.artist) if part)
    if alt and alt != query:
        queries.append(f"ytsearch8:{alt}")
    return queries


def _extract_search_entries(url: str) -> list[dict]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": False,
        "playlistend": 8,
        "ignoreerrors": True,
    }
    options.update(_auth_options())
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    entries = info.get("entries")
    if entries:
        return [entry for entry in entries if entry]
    if info.get("id"):
        return [info]
    return []


def _video_url(info: dict) -> str:
    video_id = str(info.get("id") or "")
    url = str(info.get("webpage_url") or info.get("url") or "")
    if url.startswith("http") and "watch" in url:
        return url
    if len(video_id) >= 11:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def _search_candidates(track: Track) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    for url in _search_queries(track):
        try:
            entries = _extract_search_entries(url)
        except (DownloadError, ExtractorError, OSError):
            continue
        for entry in entries:
            video_id = str(entry.get("id") or "")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            results.append(entry)
    return results


def _run_ffmpeg(args: list[str]) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    result = subprocess.run(
        [ffmpeg, "-y", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _convert_to_jpg(source: Path, destination: Path) -> bool:
    if source.suffix.lower() in {".jpg", ".jpeg"}:
        if source.resolve() != destination.resolve():
            destination.write_bytes(source.read_bytes())
        return destination.exists()
    return _run_ffmpeg(["-i", str(source), str(destination)])


def _embed_cover(audio: Path, cover: Path) -> bool:
    tmp = audio.with_name(f"{audio.stem}.tmp{audio.suffix}")
    ok = _run_ffmpeg(
        [
            "-i",
            str(audio),
            "-i",
            str(cover),
            "-map",
            "0:a",
            "-map",
            "1:v:0",
            "-c:a",
            "copy",
            "-c:v",
            "mjpeg",
            "-map_metadata",
            "0",
            "-disposition:v",
            "attached_pic",
            "-id3v2_version",
            "3",
            str(tmp),
        ]
    )
    if ok and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(audio)
        return True
    tmp.unlink(missing_ok=True)
    return False


def delete_sidecar_covers(output_dir: Path, track: Track) -> None:
    for path in _files_for_track(output_dir, track):
        if path.suffix.lower() in COVER_EXTENSIONS:
            path.unlink(missing_ok=True)


def cleanup_non_audio(output_dir: Path, track: Track, audio_format: str) -> None:
    keep = f".{audio_format}".lower()
    for path in _files_for_track(output_dir, track):
        if path.suffix.lower() != keep:
            path.unlink(missing_ok=True)


def extract_cover_bytes(audio: Path) -> bytes | None:
    if not audio.exists():
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio),
                "-an",
                "-c:v",
                "mjpeg",
                "-f",
                "image2pipe",
                "-frames:v",
                "1",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode == 0 and result.stdout:
        return result.stdout
    return None


def _download_cover_bytes(url: str) -> tuple[bytes, str] | None:
    if not url.lower().startswith("https://"):
        return None
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": COVER_UA,
            "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
            "Referer": "https://open.spotify.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(COVER_MAX_BYTES + 1)
            content_type = (response.headers.get("Content-Type") or "").lower()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not data or len(data) > COVER_MAX_BYTES:
        return None
    suffix = ".jpg"
    if "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    return data, suffix


def embed_spotify_cover(
    output_dir: Path,
    track: Track,
    audio: Path | None,
    log: ProgressCallback | None = None,
) -> None:
    cleanup_non_audio(output_dir, track, audio.suffix.lstrip(".") if audio else "mp3")
    if not audio or not track.cover_url:
        return
    if log:
        log("Pobieram okładkę ze Spotify")
    fetched = _download_cover_bytes(track.cover_url)
    if not fetched:
        if log:
            log("Nie udało się pobrać okładki ze Spotify")
        return
    data, suffix = fetched
    cover: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
            dir=tempfile.gettempdir(),
        ) as handle:
            handle.write(data)
            cover = Path(handle.name)
        if _embed_cover(audio, cover) and log:
            log("Okładka ze Spotify zapisana w MP3")
        elif log:
            log("Nie udało się osadzić okładki w MP3")
    finally:
        if cover is not None:
            cover.unlink(missing_ok=True)
        cleanup_non_audio(output_dir, track, audio.suffix.lstrip("."))


def _embed_cover_safe(
    output_dir: Path,
    track: Track,
    audio: Path,
    log: ProgressCallback | None = None,
) -> None:
    try:
        embed_spotify_cover(output_dir, track, audio, log)
    except OSError as exc:
        if log:
            log(f"Nie udało się osadzić okładki: {exc}")


def _download_matched(
    track: Track,
    output_dir: Path,
    audio_format: str,
    log: ProgressCallback | None = None,
) -> None:
    candidates = _search_candidates(track)
    chosen = pick_best_candidate(track, candidates)
    if not chosen:
        if log and candidates:
            first = candidates[0]
            log(f"Żaden wynik nie pasuje. Najbliższy: {candidate_label(first)}")
        raise DownloadFailed(
            f"Nie znalazłem na YouTube utworu pasującego do: {track.display_name()}"
        )
    video_url = _video_url(chosen)
    if not video_url:
        raise DownloadFailed(f"Nie znalazłem linku do: {track.display_name()}")
    if log:
        log(f"Dopasowano: {candidate_label(chosen)}")
    options = _ydl_options(output_dir, track, audio_format)
    with YoutubeDL(options) as ydl:
        ydl.download([video_url])
    cleanup_non_audio(output_dir, track, audio_format)


def download_track(
    track: Track,
    output_dir: Path,
    audio_format: str = "mp3",
    log: ProgressCallback | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    already = existing_file(output_dir, track, audio_format)
    if already:
        cleanup_non_audio(output_dir, track, audio_format)
        _embed_cover_safe(output_dir, track, already, log)
        if log:
            log(f"Pomijam (już jest): {already.name}")
        return already

    last_error: Exception | None = None
    try:
        _download_matched(track, output_dir, audio_format, log)
    except DownloadFailed:
        raise
    except (DownloadError, ExtractorError, OSError) as exc:
        last_error = exc

    found = existing_file(output_dir, track, audio_format)
    if found:
        cleanup_non_audio(output_dir, track, audio_format)
        _embed_cover_safe(output_dir, track, found, log)
        return found

    if last_error and _is_bot_block(last_error):
        raise DownloadFailed(f"Nie udało się pobrać: {track.display_name()}. {BOT_HINT}")
    detail = f" ({last_error})" if last_error else ""
    raise DownloadFailed(f"Nie udało się pobrać: {track.display_name()}{detail}")


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "Brak ffmpeg. Na macOS: brew install ffmpeg\n"
            "Bez tego nie da się zapisać MP3."
        )
