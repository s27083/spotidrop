"""Wczytuje listę utworów z publicznego linku Spotify (playlista, album, utwór)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from csv_parser import Track, coerce_duration_ms

SPOTIFY_REF = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?|spotify:)(playlist|album|track)[/:]([A-Za-z0-9]+)",
    re.IGNORECASE,
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SpotifyFetchError(ValueError):
    pass


def parse_spotify_url(url: str) -> tuple[str, str]:
    text = (url or "").strip()
    match = SPOTIFY_REF.search(text)
    if not match:
        raise SpotifyFetchError(
            "To nie wygląda na link Spotify. Wklej playlistę, album albo utwór."
        )
    return match.group(1).lower(), match.group(2)


def _cover_url_from_mapping(data: dict | None) -> str:
    if not data:
        return ""
    for key in ("image", "cover", "albumArt", "album_art", "coverArt"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                url = first.get("url") or ""
                if url:
                    return url
        if isinstance(value, dict):
            url = value.get("url") or ""
            if url:
                return url
            sources = value.get("sources") or value.get("image") or []
            if isinstance(sources, list) and sources:
                item = sources[0]
                if isinstance(item, str) and item.startswith("http"):
                    return item
                if isinstance(item, dict) and item.get("url"):
                    return item["url"]
    visual = data.get("visualIdentity")
    images = visual.get("image") if isinstance(visual, dict) else None
    if isinstance(images, list):
        scored: list[tuple[int, str]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            url = image.get("url") or ""
            if not url:
                continue
            width = image.get("maxWidth") or image.get("width") or 0
            scored.append((int(width), url))
        scored.sort(reverse=True)
        if scored:
            return scored[0][1]
    return ""


def tracks_from_embed_entity(entity: dict) -> tuple[str, list[Track]]:
    name = (entity.get("name") or entity.get("title") or "Spotify").strip()
    playlist_cover = _cover_url_from_mapping(entity)
    tracks: list[Track] = []
    seen: set[tuple[str, str]] = set()
    for item in entity.get("trackList") or []:
        title = (item.get("title") or item.get("name") or "").strip()
        artist = (item.get("subtitle") or item.get("artist") or "").strip()
        album = (item.get("album") or "").strip()
        duration_ms = coerce_duration_ms(
            item.get("duration") or item.get("durationMs") or item.get("duration_ms")
        )
        if not title:
            continue
        key = (title.lower(), artist.lower())
        if key in seen:
            continue
        seen.add(key)
        tracks.append(
            Track(
                title=title,
                artist=artist,
                album=album,
                cover_url=_cover_url_from_mapping(item) or playlist_cover,
                duration_ms=duration_ms,
            )
        )
    if not tracks and (entity.get("type") == "track" or entity.get("entityType") == "track"):
        title = (entity.get("name") or entity.get("title") or "").strip()
        artist = (entity.get("subtitle") or "").strip()
        if title:
            tracks.append(
                Track(
                    title=title,
                    artist=artist,
                    cover_url=_cover_url_from_mapping(entity),
                    duration_ms=coerce_duration_ms(entity.get("duration")),
                )
            )
    if not tracks:
        raise SpotifyFetchError("Nie znalazłem utworów w tym linku Spotify.")
    return name, tracks


def _http_get(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise SpotifyFetchError(f"Spotify zwrócił błąd HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise SpotifyFetchError(f"Nie mogę połączyć się ze Spotify ({exc.reason}).") from exc


def _fetch_embed(kind: str, entity_id: str) -> tuple[str, list[Track]]:
    html = _http_get(f"https://open.spotify.com/embed/{kind}/{entity_id}")
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise SpotifyFetchError("Nie udało się odczytać danych z osadzanej strony Spotify.")
    payload = json.loads(match.group(1))
    entity = payload["props"]["pageProps"]["state"]["data"]["entity"]
    return tracks_from_embed_entity(entity)


def _artist_names(artists) -> str:
    names = []
    for artist in artists or []:
        name = getattr(artist, "name", None) or (artist.get("name") if isinstance(artist, dict) else None)
        if name:
            names.append(name)
    return ", ".join(names)


def _pick_cover_url(images) -> str:
    scored: list[tuple[int, str]] = []
    for image in images or ():
        url = getattr(image, "url", None) or (image.get("url") if isinstance(image, dict) else None)
        if not url:
            continue
        width = getattr(image, "width", None)
        if width is None and isinstance(image, dict):
            width = image.get("width")
        scored.append((int(width or 0), url))
    scored.sort(reverse=True)
    return scored[0][1] if scored else ""


def _cover_url_from_scraper(track) -> str:
    url = _pick_cover_url(getattr(track, "images", None))
    if url:
        return url
    album = getattr(track, "album", None)
    return _pick_cover_url(getattr(album, "images", None) if album is not None else None)


def _track_from_scraper(item, fallback_cover: str = "") -> Track | None:
    track = getattr(item, "track", item)
    if track is None:
        return None
    title = (getattr(track, "name", None) or "").strip()
    if not title:
        return None
    artist = _artist_names(getattr(track, "artists", None))
    album_obj = getattr(track, "album", None)
    album = (getattr(album_obj, "name", None) or "").strip() if album_obj else ""
    duration_ms = coerce_duration_ms(
        getattr(track, "duration_ms", 0) or getattr(track, "duration", 0)
    )
    return Track(
        title=title,
        artist=artist,
        album=album,
        cover_url=_cover_url_from_scraper(track) or fallback_cover,
        duration_ms=duration_ms,
    )


def _fetch_with_scraper(kind: str, url: str) -> tuple[str, list[Track]]:
    from spotify_scraper import SpotifyClient

    fallback_cover = ""
    with SpotifyClient() as client:
        if kind == "playlist":
            playlist = client.get_playlist(url, max_tracks=None)
            name = playlist.name or "Playlista Spotify"
            raw_items = playlist.tracks
        elif kind == "album":
            album = client.get_album(url)
            name = album.name or "Album Spotify"
            raw_items = album.tracks
            fallback_cover = _pick_cover_url(getattr(album, "images", None))
        else:
            track = client.get_track(url)
            converted = _track_from_scraper(track)
            if not converted:
                raise SpotifyFetchError("Nie udało się odczytać tego utworu.")
            return converted.title, [converted]

    tracks: list[Track] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items or []:
        converted = _track_from_scraper(item, fallback_cover=fallback_cover)
        if not converted:
            continue
        key = (converted.title.lower(), converted.artist.lower())
        if key in seen:
            continue
        seen.add(key)
        tracks.append(converted)
    if not tracks:
        raise SpotifyFetchError("Ta playlista nie ma utworów do pobrania.")
    return name, tracks


def fetch_spotify_tracks(url: str) -> tuple[str, list[Track]]:
    kind, entity_id = parse_spotify_url(url)
    errors: list[str] = []
    try:
        return _fetch_with_scraper(kind, url)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    try:
        return _fetch_embed(kind, entity_id)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    detail = " ".join(errors).strip()
    raise SpotifyFetchError(
        "Nie udało się wczytać listy ze Spotify. "
        "Sprawdź, czy playlista jest publiczna. "
        + (detail if detail else "")
    )
