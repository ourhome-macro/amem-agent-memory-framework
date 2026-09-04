from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


def make_track_id(bvid: str, cid: Optional[int] = None) -> str:
    bvid = normalize_bvid(bvid)
    if cid is None:
        return f"bili:{bvid}"
    return f"bili:{bvid}:cid:{cid}"


def normalize_bvid(bvid: str) -> str:
    value = (bvid or "").strip()
    if value[:2].lower() == "bv":
        return f"BV{value[2:]}"
    return value


@dataclass
class Track:
    bvid: str
    title: str
    owner: str = ""
    owner_mid: Optional[int] = None
    cover: str = ""
    duration: int = 0
    cid: Optional[int] = None
    track_id: Optional[str] = None
    play_count: int = 0
    published_at: Optional[str] = None
    page: Optional[int] = None
    page_title: Optional[str] = None
    source: str = "bili"

    def __post_init__(self) -> None:
        self.bvid = normalize_bvid(self.bvid)
        if self.track_id is None:
            self.track_id = make_track_id(self.bvid, self.cid)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Track":
        bvid = str(payload.get("bvid", "")).strip()
        cid = payload.get("cid")
        return cls(
            track_id=payload.get("trackId") or payload.get("track_id"),
            bvid=bvid,
            cid=int(cid) if cid not in (None, "") else None,
            title=str(payload.get("title", "")).strip(),
            owner=str(payload.get("owner", "")).strip(),
            owner_mid=(
                int(payload.get("ownerMid") or payload.get("owner_mid"))
                if payload.get("ownerMid") or payload.get("owner_mid")
                else None
            ),
            cover=str(payload.get("cover", "")).strip(),
            duration=int(payload.get("duration") or 0),
            play_count=int(payload.get("playCount") or payload.get("play_count") or 0),
            published_at=payload.get("publishedAt") or payload.get("published_at"),
            page=payload.get("page"),
            page_title=payload.get("pageTitle") or payload.get("page_title"),
            source=payload.get("source") or "bili",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trackId": self.track_id,
            "bvid": self.bvid,
            "cid": self.cid,
            "title": self.title,
            "owner": self.owner,
            "ownerMid": self.owner_mid,
            "cover": self.cover,
            "duration": self.duration,
            "playCount": self.play_count,
            "publishedAt": self.published_at,
            "page": self.page,
            "pageTitle": self.page_title,
            "source": self.source,
        }


@dataclass
class VideoInfo:
    bvid: str
    cid: int
    title: str
    duration: int
    owner: str
    cover: str
    owner_mid: Optional[int] = None
    play_count: int = 0
    published_at: Optional[str] = None

    def to_track(self) -> Track:
        return Track(
            bvid=self.bvid,
            cid=self.cid,
            title=self.title,
            owner=self.owner,
            owner_mid=self.owner_mid,
            cover=self.cover,
            duration=self.duration,
            play_count=self.play_count,
            published_at=self.published_at,
            page=1,
        )


@dataclass
class VideoDetail:
    info: VideoInfo
    pages: list[Track]

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.info.to_track().to_dict(),
            "pages": [track.to_dict() for track in self.pages],
        }


@dataclass
class BiliUserProfile:
    mid: int
    name: str
    face: str = ""
    level: int = 0
    vip_type: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mid": self.mid,
            "name": self.name,
            "face": self.face,
            "level": self.level,
            "vipType": self.vip_type,
        }


@dataclass
class FavoriteFolder:
    media_id: int
    title: str
    mid: int = 0
    fid: Optional[int] = None
    cover: str = ""
    media_count: int = 0
    attr: int = 0
    favorite_state: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mediaId": self.media_id,
            "id": self.media_id,
            "fid": self.fid,
            "mid": self.mid,
            "title": self.title,
            "cover": self.cover,
            "mediaCount": self.media_count,
            "attr": self.attr,
            "favoriteState": self.favorite_state,
        }


@dataclass
class AudioStreamInfo:
    url: str
    backup_urls: list[str]
    duration: int
    bitrate: int
    sample_rate: int
    channels: int
    init_range: str = ""
    index_range: str = ""
    quality: str = "auto"
    actual_quality: str = "standard"
    codec: str = "aac"
    fallback: bool = False
    stream_id: Optional[int] = None
    available_qualities: Optional[list[str]] = None

    @property
    def stream_identity(self) -> str:
        return f"{self.stream_id or 0}:{self.bitrate}:{self.codec}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "backupUrls": self.backup_urls,
            "duration": self.duration,
            "bitrate": self.bitrate,
            "sampleRate": self.sample_rate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "quality": self.quality,
            "actualQuality": self.actual_quality,
            "actual_quality": self.actual_quality,
            "codec": self.codec,
            "fallback": self.fallback,
            "streamId": self.stream_id,
            "availableAudioQualities": self.available_qualities or [],
        }
