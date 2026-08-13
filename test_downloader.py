import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csv_parser import Track
from downloader import (
    _ydl_options,
    cover_path,
    delete_sidecar_covers,
    embed_spotify_cover,
    existing_cover,
    existing_file,
    extract_cover_bytes,
    track_stem,
)


class FakeImageResponse:
    def __init__(self, data: bytes, content_type: str = "image/jpeg") -> None:
        self._data = data
        self.headers = {"Content-Type": content_type}

    def read(self, _n: int = -1) -> bytes:
        return self._data

    def __enter__(self) -> "FakeImageResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


class DownloaderPathTests(unittest.TestCase):
    def test_track_stem_and_cover_path(self) -> None:
        track = Track(title="300G", artist="Kobosil", album="X")
        self.assertEqual(track_stem(track), "Kobosil - 300G")
        folder = Path("/tmp/music")
        self.assertEqual(cover_path(folder, track), folder / "Kobosil - 300G.jpg")

    def test_existing_cover_finds_jpg(self) -> None:
        track = Track(title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song.mp3"
            jpg = folder / "Artist - Song.jpg"
            audio.write_bytes(b"fake")
            jpg.write_bytes(b"img")
            self.assertEqual(existing_file(folder, track, "mp3"), audio)
            self.assertEqual(existing_cover(folder, track), jpg)

    def test_ydl_does_not_write_youtube_thumbnail(self) -> None:
        options = _ydl_options(
            Path("/tmp"),
            Track(title="T", artist="A"),
            "mp3",
        )
        self.assertFalse(options["writethumbnail"])
        self.assertFalse(options["writeinfojson"])
        self.assertFalse(options["writesubtitles"])
        keys = [item["key"] for item in options["postprocessors"]]
        self.assertNotIn("EmbedThumbnail", keys)
        self.assertNotIn("FFmpegThumbnailsConvertor", keys)
        self.assertIn("android", options["extractor_args"]["youtube"]["player_client"])

    def test_ydl_uses_cookiefile_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cookies = Path(raw) / "cookies.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with patch.dict(os.environ, {"YTDLP_COOKIES": str(cookies)}, clear=False):
                options = _ydl_options(
                    Path("/tmp"),
                    Track(title="T", artist="A"),
                    "mp3",
                )
            self.assertEqual(options["cookiefile"], str(cookies))

    def test_embed_spotify_cover_without_url_deletes_sidecar(self) -> None:
        track = Track(title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song.mp3"
            jpg = folder / "Artist - Song.jpg"
            audio.write_bytes(b"fake")
            jpg.write_bytes(b"img")
            embed_spotify_cover(folder, track, audio)
            self.assertTrue(audio.exists())
            self.assertFalse(jpg.exists())

    def test_embed_spotify_cover_fetches_url_then_deletes_file(self) -> None:
        track = Track(
            title="Song",
            artist="Artist",
            cover_url="https://i.scdn.co/image/cover",
        )
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song.mp3"
            audio.write_bytes(b"fake")
            with patch(
                "downloader.urllib.request.urlopen",
                return_value=FakeImageResponse(b"\xff\xd8fakejpg"),
            ):
                embed_spotify_cover(folder, track, audio)
            self.assertTrue(audio.exists())
            self.assertIsNone(existing_cover(folder, track))
            self.assertFalse(any(folder.glob("*.jpg")))
            self.assertFalse(any(folder.glob("*.png")))
            self.assertFalse(any(folder.glob("*.webp")))

    def test_delete_sidecar_covers_leaves_mp3(self) -> None:
        track = Track(title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song.mp3"
            jpg = folder / "Artist - Song.jpg"
            webp = folder / "Artist - Song.webp"
            audio.write_bytes(b"fake")
            jpg.write_bytes(b"img")
            webp.write_bytes(b"img")
            delete_sidecar_covers(folder, track)
            self.assertTrue(audio.exists())
            self.assertFalse(jpg.exists())
            self.assertFalse(webp.exists())

    def test_cleanup_non_audio_removes_jpg(self) -> None:
        from downloader import cleanup_non_audio

        track = Track(title="Song", artist="Artist")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song.mp3"
            jpg = folder / "Artist - Song.jpg"
            audio.write_bytes(b"fake")
            jpg.write_bytes(b"img")
            cleanup_non_audio(folder, track, "mp3")
            self.assertTrue(audio.exists())
            self.assertFalse(jpg.exists())

    def test_cleanup_ignores_glob_brackets_in_title(self) -> None:
        from downloader import cleanup_non_audio

        track = Track(title="Song [Live]", artist="Artist")
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            audio = folder / "Artist - Song [Live].mp3"
            jpg = folder / "Artist - Song [Live].jpg"
            other = folder / "Artist - Song L.jpg"
            audio.write_bytes(b"fake")
            jpg.write_bytes(b"img")
            other.write_bytes(b"keep")
            cleanup_non_audio(folder, track, "mp3")
            self.assertTrue(audio.exists())
            self.assertFalse(jpg.exists())
            self.assertTrue(other.exists())

    def test_extract_cover_bytes_from_mp3(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as raw:
            audio = Path(raw) / "Artist - Song.mp3"
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
                    "color=c=red:s=32x32:d=0.1",
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
            data = extract_cover_bytes(audio)
            self.assertTrue(data)
            self.assertGreater(len(data), 50)
