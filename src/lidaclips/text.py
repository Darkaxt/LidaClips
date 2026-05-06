import re
import unicodedata


WINDOWS_BAD_CHARS = r'\/<>?*:|"'
WINDOWS_TRANSLATION = str.maketrans({char: " " for char in WINDOWS_BAD_CHARS})


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^\w\s&+-]", " ", ascii_text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def safe_filename(value: str | None, fallback: str = "untitled") -> str:
    cleaned = (value or fallback).translate(WINDOWS_TRANSLATION)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned or fallback


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(\d{4})\b", value)
    if not match:
        return None
    return int(match.group(1))


def compact_track_number(track_number: str | int | None, absolute_track_number: int | None = None) -> str:
    if absolute_track_number:
        return str(absolute_track_number).zfill(2)
    if track_number is None:
        return "00"
    match = re.search(r"\d+", str(track_number))
    if not match:
        return "00"
    return match.group(0).zfill(2)
