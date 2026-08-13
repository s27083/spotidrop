#!/usr/bin/env python3
"""Pobieranie utworów z YouTube / YouTube Music na podstawie CSV ze Spotify albo artysty i tytułu."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from csv_parser import Track, parse_spotify_csv
from downloader import DownloadFailed, download_track, ensure_ffmpeg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobierz utwory z YouTube Music / YouTube na podstawie CSV ze Spotify albo artysty i tytułu.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady:
  python main.py --csv playlist.csv
  python main.py --artist "Daft Punk" --title "Get Lucky"
  python main.py --csv playlist.csv --output muzyka --limit 5
        """,
    )
    parser.add_argument("--csv", help="Plik CSV ze Spotify (tytuł, artysta, album...)")
    parser.add_argument("--url", help="Link do playlisty, albumu albo utworu Spotify")
    parser.add_argument("--artist", help="Artysta (tryb pojedynczego utworu)")
    parser.add_argument("--title", help="Tytuł utworu (tryb pojedynczego utworu)")
    parser.add_argument(
        "--output",
        default="downloads",
        help="Folder zapisu (domyślnie: downloads)",
    )
    parser.add_argument(
        "--format",
        dest="audio_format",
        default="mp3",
        choices=["mp3", "m4a", "opus", "flac", "wav"],
        help="Format audio (domyślnie: mp3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Pobierz tylko pierwsze N utworów z CSV (0 = wszystkie)",
    )
    return parser.parse_args()


def collect_tracks(args: argparse.Namespace) -> list[Track]:
    if args.csv:
        tracks = parse_spotify_csv(args.csv)
        if args.limit and args.limit > 0:
            tracks = tracks[: args.limit]
        return tracks

    if args.url:
        from spotify import fetch_spotify_tracks

        _name, tracks = fetch_spotify_tracks(args.url)
        if args.limit and args.limit > 0:
            tracks = tracks[: args.limit]
        return tracks

    if args.artist or args.title:
        if not args.artist or not args.title:
            raise ValueError("Podaj jednocześnie --artist i --title.")
        return [Track(title=args.title.strip(), artist=args.artist.strip())]

    return interactive_tracks()


def interactive_tracks() -> list[Track]:
    print("YouTube Music downloader")
    print("1) CSV ze Spotify")
    print("2) Artysta + tytuł")
    choice = input("Wybór [1/2]: ").strip()

    if choice == "1":
        path = input("Ścieżka do CSV: ").strip().strip('"')
        return parse_spotify_csv(path)

    if choice == "2":
        artist = input("Artysta: ").strip()
        title = input("Tytuł: ").strip()
        if not artist or not title:
            raise ValueError("Artysta i tytuł są wymagane.")
        return [Track(title=title, artist=artist)]

    raise ValueError("Nieznany wybór. Wpisz 1 albo 2.")


def main() -> int:
    args = parse_args()
    if not args.csv and not args.url and not args.artist and not args.title:
        from gui import run_gui

        run_gui()
        return 0

    try:
        ensure_ffmpeg()
        tracks = collect_tracks(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output)
    ok = 0
    failed = 0

    print(f"Do pobrania: {len(tracks)} utwór(ów) → {output_dir.resolve()}")

    for index, track in enumerate(tracks, start=1):
        prefix = f"[{index}/{len(tracks)}]"
        print(f"{prefix} Szukam: {track.display_name()}")
        try:
            path = download_track(
                track,
                output_dir=output_dir,
                audio_format=args.audio_format,
                log=lambda message: print(f"{prefix} {message}"),
            )
            print(f"{prefix} OK: {path.name}")
            ok += 1
        except DownloadFailed as exc:
            print(f"{prefix} FAIL: {exc}")
            failed += 1

    print(f"\nGotowe. Pobrane: {ok}, błędy: {failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
