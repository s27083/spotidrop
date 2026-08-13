import tempfile
import unittest
from pathlib import Path

from csv_parser import parse_spotify_csv


class ParseSpotifyCsvTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w",
            suffix=".csv",
            delete=False,
            encoding="utf-8",
        )
        handle.write(content)
        handle.close()
        return Path(handle.name)

    def test_exportify_headers(self) -> None:
        path = self._write(
            "Track Name,Artist Name(s),Album Name\n"
            "Get Lucky,Daft Punk,Random Access Memories\n"
        )
        tracks = parse_spotify_csv(path)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Get Lucky")
        self.assertEqual(tracks[0].artist, "Daft Punk")
        self.assertEqual(tracks[0].album, "Random Access Memories")
        self.assertEqual(tracks[0].search_query(), "Daft Punk - Get Lucky")

    def test_duration_column(self) -> None:
        path = self._write(
            "Track Name,Artist Name(s),Track Duration (ms)\n"
            "Get Lucky,Daft Punk,248373\n"
        )
        tracks = parse_spotify_csv(path)
        self.assertEqual(tracks[0].duration_ms, 248373)

    def test_simple_headers_and_dedup(self) -> None:
        path = self._write(
            "Title,Artist,Album\n"
            "Take On Me,a-ha,Hunting High and Low\n"
            "Take On Me,a-ha,Hunting High and Low\n"
        )
        tracks = parse_spotify_csv(path)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "a-ha")

    def test_polish_headers(self) -> None:
        path = self._write("Tytuł,Artysta,Płyta\nHej,Artysta Test,Album Test\n")
        tracks = parse_spotify_csv(path)
        self.assertEqual(tracks[0].title, "Hej")
        self.assertEqual(tracks[0].artist, "Artysta Test")
        self.assertEqual(tracks[0].album, "Album Test")

    def test_skips_empty_title(self) -> None:
        path = self._write("Track Name,Artist\n,Nobody\nSong,Somebody\n")
        tracks = parse_spotify_csv(path)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Song")

    def test_missing_title_column(self) -> None:
        path = self._write("Foo,Bar\n1,2\n")
        with self.assertRaises(ValueError):
            parse_spotify_csv(path)

    def test_parse_text(self) -> None:
        from csv_parser import parse_spotify_csv_text

        tracks = parse_spotify_csv_text(
            "Track Name,Artist Name(s),Album Name\nGet Lucky,Daft Punk,Random Access Memories\n"
        )
        self.assertEqual(tracks[0].artist, "Daft Punk")


if __name__ == "__main__":
    unittest.main()
