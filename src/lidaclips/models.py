from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ClipTarget:
    lidarr_track_id: int
    artist_id: int
    album_id: int
    artist: str
    album: str
    album_year: Optional[int]
    title: str
    track_number: str
    absolute_track_number: int
    duration: Optional[int]
    source_file_path: Optional[str]
