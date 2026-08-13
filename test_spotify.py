import unittest
from types import SimpleNamespace

from spotify import (
    SpotifyFetchError,
    _track_from_scraper,
    parse_spotify_url,
    tracks_from_embed_entity,
)


class SpotifyUrlTests(unittest.TestCase):
    def test_playlist_https(self) -> None:
        kind, entity_id = parse_spotify_url(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"
        )
        self.assertEqual(kind, "playlist")
        self.assertEqual(entity_id, "37i9dQZF1DXcBWIGoYBM5M")

    def test_intl_and_uri(self) -> None:
        kind, entity_id = parse_spotify_url(
            "https://open.spotify.com/intl-pl/album/4aawyAB9vmqN3uQ7FjRGTy"
        )
        self.assertEqual(kind, "album")
        kind, entity_id = parse_spotify_url("spotify:track:0VjIjW4GlUZAMYd2vXMi3b")
        self.assertEqual(kind, "track")
        self.assertEqual(entity_id, "0VjIjW4GlUZAMYd2vXMi3b")

    def test_invalid(self) -> None:
        with self.assertRaises(SpotifyFetchError):
            parse_spotify_url("https://youtube.com/watch?v=abc")

    def test_embed_entity(self) -> None:
        name, tracks = tracks_from_embed_entity(
            {
                "name": "Test lista",
                "visualIdentity": {
                    "image": [{"url": "https://i.scdn.co/image/playlist", "maxWidth": 300}]
                },
                "trackList": [
                    {"title": "Get Lucky", "subtitle": "Daft Punk", "album": "RAM", "duration": 248},
                    {"title": "Get Lucky", "subtitle": "Daft Punk"},
                    {"title": "", "subtitle": "Nobody"},
                ],
            }
        )
        self.assertEqual(name, "Test lista")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "Daft Punk")
        self.assertEqual(tracks[0].album, "RAM")
        self.assertEqual(tracks[0].cover_url, "https://i.scdn.co/image/playlist")
        self.assertEqual(tracks[0].duration_ms, 248000)

    def test_embed_track_cover_preferred_over_playlist(self) -> None:
        _, tracks = tracks_from_embed_entity(
            {
                "name": "Lista",
                "visualIdentity": {
                    "image": [{"url": "https://i.scdn.co/image/playlist", "maxWidth": 300}]
                },
                "trackList": [
                    {
                        "title": "Song",
                        "subtitle": "Artist",
                        "image": "https://i.scdn.co/image/track",
                    }
                ],
            }
        )
        self.assertEqual(tracks[0].cover_url, "https://i.scdn.co/image/track")

    def test_scraper_track_cover_from_album_images(self) -> None:
        track = SimpleNamespace(
            name="Song",
            artists=[SimpleNamespace(name="Artist")],
            album=SimpleNamespace(
                name="LP",
                images=[
                    SimpleNamespace(url="https://i.scdn.co/image/small", width=64),
                    SimpleNamespace(url="https://i.scdn.co/image/large", width=640),
                ],
            ),
            images=(),
            duration_ms=248000,
        )
        converted = _track_from_scraper(track)
        self.assertIsNotNone(converted)
        self.assertEqual(converted.artist, "Artist")
        self.assertEqual(converted.cover_url, "https://i.scdn.co/image/large")
        self.assertEqual(converted.duration_ms, 248000)

    def test_scraper_picks_largest_cover(self) -> None:
        track = SimpleNamespace(
            name="Song",
            artists=[SimpleNamespace(name="Artist")],
            album=None,
            images=[
                SimpleNamespace(url="https://i.scdn.co/image/small", width=64),
                SimpleNamespace(url="https://i.scdn.co/image/large", width=640),
            ],
        )
        converted = _track_from_scraper(track)
        self.assertEqual(converted.cover_url, "https://i.scdn.co/image/large")

    def test_scraper_uses_fallback_cover(self) -> None:
        track = SimpleNamespace(
            name="Song",
            artists=[SimpleNamespace(name="Artist")],
            album=None,
            images=(),
        )
        converted = _track_from_scraper(
            track, fallback_cover="https://i.scdn.co/image/album"
        )
        self.assertEqual(converted.cover_url, "https://i.scdn.co/image/album")
