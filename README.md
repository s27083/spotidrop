# YouTube Music Downloader

Interfejs można hostować na **GitHub Pages**. Same pobieranie zawsze idzie na Twoim komputerze (`yt-dlp` + `ffmpeg`) — Pages nie uruchamia Pythona.

## Lokalnie

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python gui.py
```

Otworzy się http://127.0.0.1:8765

## GitHub Pages

1. Wypchnij repo na GitHub.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Po pushu na `main` workflow wrzuci `web/index.html`.
4. Wejdź na `https://<user>.github.io/<repo>/`.
5. Na tym samym Macu odpal `.venv/bin/python gui.py` — strona z Pages łączy się z `http://127.0.0.1:8765` i zapisuje MP3 u Ciebie na dysku.

Bez odpalonego `gui.py` interfejs się wczyta, ale pobieranie nie ruszy.

Własny backend HTTPS: `https://...github.io/repo/?api=https://twoj-serwer`.

## CLI

```bash
.venv/bin/python main.py --csv playlist.csv
.venv/bin/python main.py --url "https://open.spotify.com/playlist/...."
.venv/bin/python main.py --artist "Daft Punk" --title "Get Lucky"
```
