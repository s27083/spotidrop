"""Dopasowuje wyniki YouTube do utworu ze Spotify."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from csv_parser import Track

MIN_SCORE = 48
NOISE_WORDS = {
    "official",
    "audio",
    "video",
    "lyrics",
    "lyric",
    "visualizer",
    "hd",
    "hq",
    "4k",
    "feat",
    "ft",
    "featuring",
    "the",
    "a",
    "an",
    "and",
}
EXTRA_WORDS = (
    "live",
    "remix",
    "karaoke",
    "cover",
    "nightcore",
    "slowed",
    "sped",
    "mashup",
    "reaction",
    "tutorial",
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold()
    value = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", value)
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    tokens = [token for token in value.split() if token and token not in NOISE_WORDS]
    return " ".join(tokens)


def _tokens(text: str) -> list[str]:
    return [token for token in _normalize(text).split() if len(token) > 1]


def token_recall(expected: str, haystack: str) -> float:
    needles = _tokens(expected)
    if not needles:
        return 0.0
    hay = set(_tokens(haystack))
    return sum(1 for token in needles if token in hay) / len(needles)


def _artist_parts(artist: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+&\s+|\s+and\s+|\s+x\s+|\s*/\s*", artist or "", flags=re.I)
    return [part.strip() for part in parts if part.strip()]


def artist_mentioned(artist: str, *fields: str) -> bool:
    blob = _normalize(" ".join(field for field in fields if field))
    if not blob:
        return False
    for name in _artist_parts(artist):
        normalized = _normalize(name)
        if normalized and normalized in blob:
            return True
        tokens = _tokens(name)
        if tokens and all(token in blob.split() or token in blob for token in tokens):
            return True
    return False


def _duration_score(expected_ms: int, yt_seconds: float | None) -> tuple[int, bool]:
    if not expected_ms or expected_ms < 1000:
        return 0, False
    if yt_seconds is None:
        return -4, False
    expected_s = expected_ms / 1000
    if expected_s <= 0:
        return 0, False
    ratio = yt_seconds / expected_s
    if yt_seconds >= 15 * 60 and expected_s < 8 * 60:
        return 0, True
    if ratio > 2.2 or ratio < 0.45:
        return 0, True
    diff = abs(yt_seconds - expected_s)
    if diff <= 4:
        return 25, False
    if diff <= 12:
        return 18, False
    if diff <= 25:
        return 8, False
    if diff <= 45:
        return 0, False
    return -18, False


def _extra_penalty(expected_title: str, yt_title: str) -> int:
    expected = _normalize(expected_title)
    found = _normalize(yt_title)
    penalty = 0
    for word in EXTRA_WORDS:
        if word in found.split() and word not in expected.split():
            penalty += 16
    if "hour" in found.split() and "hour" not in expected.split():
        penalty += 24
    return penalty


def score_candidate(track: Track, info: dict) -> float:
    yt_title = str(info.get("title") or "")
    uploader = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    if isinstance(uploader, list):
        uploader = " ".join(str(item) for item in uploader)
    uploader = str(uploader)

    title_recall = token_recall(track.title, yt_title)
    title_ratio = SequenceMatcher(
        None, _normalize(track.title), _normalize(yt_title)
    ).ratio()
    if title_recall < 0.5 and title_ratio < 0.42:
        return -1

    artist_ok = artist_mentioned(track.artist, yt_title, uploader)
    if not artist_ok:
        return -1

    duration_points, reject = _duration_score(track.duration_ms, info.get("duration"))
    if reject:
        return -1

    score = title_recall * 40 + title_ratio * 20 + 22 + duration_points
    score -= _extra_penalty(track.title, yt_title)
    if _normalize(uploader).endswith("topic"):
        score += 8
    return score


def pick_best_candidate(track: Track, candidates: list[dict]) -> dict | None:
    ranked: list[tuple[float, dict]] = []
    for item in candidates:
        if not item:
            continue
        score = score_candidate(track, item)
        if score >= MIN_SCORE:
            ranked.append((score, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def candidate_label(info: dict) -> str:
    title = (info.get("title") or "bez tytułu").strip()
    seconds = info.get("duration")
    if not seconds:
        return title
    minutes, rest = divmod(int(seconds), 60)
    return f"{title} ({minutes}:{rest:02d})"
