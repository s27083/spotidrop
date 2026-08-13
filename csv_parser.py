"""Parsuje CSV ze Spotify (i podobne eksporty) do listy utworów."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


TITLE_ALIASES = {
    "track name",
    "track_name",
    "track",
    "title",
    "name",
    "song",
    "song name",
    "utwór",
    "tytuł",
    "nazwa utworu",
}

ARTIST_ALIASES = {
    "artist name(s)",
    "artist names",
    "artist name",
    "artist_name",
    "artist",
    "artists",
    "album artist name(s)",
    "album artist",
    "artysta",
    "wykonawca",
}

ALBUM_ALIASES = {
    "album name",
    "album_name",
    "album",
    "album title",
    "płyta",
}

DURATION_ALIASES = {
    "track duration (ms)",
    "duration (ms)",
    "duration_ms",
    "duration",
    "length",
    "time",
    "ms",
    "czas trwania",
    "długość",
}


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    album: str = ""
    cover_url: str = ""
    duration_ms: int = 0

    def search_query(self) -> str:
        query = f"{self.artist} - {self.title}".strip(" -")
        return " ".join(query.split())

    def display_name(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} — {self.title}"
        return self.title or self.artist


def _normalize_header(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").split())


def _pick_column(headers: list[str], aliases: set[str]) -> str | None:
    normalized = {_normalize_header(h): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def coerce_duration_ms(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = int(value)
        if number <= 0:
            return 0
        return number if number >= 10_000 else number * 1000
    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        return coerce_duration_ms(int(text))
    parts = text.replace(",", ".").split(":")
    try:
        if len(parts) == 2:
            return int((int(parts[0]) * 60 + float(parts[1])) * 1000)
        if len(parts) == 3:
            return int((int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])) * 1000)
        return coerce_duration_ms(float(text))
    except ValueError:
        return 0


def _tracks_from_reader(reader: csv.DictReader) -> list[Track]:
    if not reader.fieldnames:
        raise ValueError("CSV nie ma nagłówków kolumn.")

    headers = list(reader.fieldnames)
    title_col = _pick_column(headers, TITLE_ALIASES)
    artist_col = _pick_column(headers, ARTIST_ALIASES)
    album_col = _pick_column(headers, ALBUM_ALIASES)
    duration_col = _pick_column(headers, DURATION_ALIASES)

    if not title_col:
        raise ValueError(
            "Nie znalazłem kolumny z tytułem. "
            f"Dostępne kolumny: {', '.join(headers)}"
        )

    tracks: list[Track] = []
    seen: set[tuple[str, str]] = set()

    for row in reader:
        title = (row.get(title_col) or "").strip()
        artist = (row.get(artist_col) or "").strip() if artist_col else ""
        album = (row.get(album_col) or "").strip() if album_col else ""
        duration_ms = coerce_duration_ms(row.get(duration_col) if duration_col else 0)

        if not title:
            continue

        key = (title.lower(), artist.lower())
        if key in seen:
            continue
        seen.add(key)
        tracks.append(
            Track(title=title, artist=artist, album=album, duration_ms=duration_ms)
        )

    if not tracks:
        raise ValueError("CSV nie zawiera żadnych utworów z tytułem.")

    return tracks


def parse_spotify_csv_text(text: str) -> list[Track]:
    if text.startswith("\ufeff"):
        text = text[1:]
    return _tracks_from_reader(csv.DictReader(io.StringIO(text)))


def parse_spotify_csv(path: str | Path) -> list[Track]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        return _tracks_from_reader(csv.DictReader(handle))
