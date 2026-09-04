from __future__ import annotations

import html
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from models import BiliUserProfile, FavoriteFolder, Track, VideoDetail, VideoInfo, normalize_bvid


TAG_RE = re.compile(r"<[^>]+>")
SUBTITLE_PATH_MARKERS = ("/bfs/subtitle/", "/bfs/ai_subtitle/")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return TAG_RE.sub("", html.unescape(str(value))).strip()


def normalize_cover(url: Any) -> str:
    if not url:
        return ""
    value = str(url).strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://"):
        return f"https://{value[7:]}"
    return value


def is_valid_subtitle_url(url: Any) -> bool:
    value = normalize_cover(url)
    if not value:
        return False
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    # Bilibili's manually uploaded subtitles end in .json, while AI subtitle
    # payloads use an extensionless /bfs/ai_subtitle/prod/... URL.
    return parsed.scheme == "https" and host.endswith(".hdslb.com") and any(
        marker in path for marker in SUBTITLE_PATH_MARKERS
    )


def parse_duration(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if all(part.isdigit() for part in parts):
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
        return seconds
    return 0


def format_pubdate(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    china_tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(china_tz).isoformat()


def normalize_search_item(item: dict[str, Any]) -> Track:
    owner_mid = item.get("mid") or item.get("owner", {}).get("mid")
    return Track(
        bvid=str(item.get("bvid") or item.get("arcurl", "").split("/")[-1]).strip(),
        title=clean_text(item.get("title")),
        owner=clean_text(item.get("author") or item.get("owner", {}).get("name")),
        owner_mid=int(owner_mid) if owner_mid not in (None, "") else None,
        cover=normalize_cover(item.get("pic")),
        duration=parse_duration(item.get("duration")),
        play_count=int(item.get("play") or item.get("play_count") or 0),
        published_at=format_pubdate(item.get("pubdate") or item.get("senddate")),
    )


def normalize_video_detail(data: dict[str, Any]) -> VideoDetail:
    bvid = normalize_bvid(str(data.get("bvid", "")))
    title = clean_text(data.get("title"))
    owner = clean_text(data.get("owner", {}).get("name"))
    owner_mid = data.get("owner", {}).get("mid")
    cover = normalize_cover(data.get("pic"))
    duration = parse_duration(data.get("duration"))
    play_count = int(data.get("stat", {}).get("view") or 0)
    published_at = format_pubdate(data.get("pubdate") or data.get("ctime"))
    default_cid = int(data.get("cid") or 0)

    info = VideoInfo(
        bvid=bvid,
        cid=default_cid,
        title=title,
        duration=duration,
        owner=owner,
        owner_mid=int(owner_mid) if owner_mid not in (None, "") else None,
        cover=cover,
        play_count=play_count,
        published_at=published_at,
    )

    pages: list[Track] = []
    for page_item in data.get("pages") or []:
        cid = int(page_item.get("cid") or 0)
        page_no = int(page_item.get("page") or len(pages) + 1)
        page_title = clean_text(page_item.get("part"))
        track_title = title if len(data.get("pages") or []) <= 1 else f"{title} - {page_title}"
        page_cover = normalize_cover(page_item.get("first_frame")) or cover
        pages.append(
            Track(
                bvid=bvid,
                cid=cid,
                title=track_title,
                owner=owner,
                owner_mid=int(owner_mid) if owner_mid not in (None, "") else None,
                cover=page_cover,
                duration=parse_duration(page_item.get("duration")),
                play_count=play_count,
                published_at=published_at,
                page=page_no,
                page_title=page_title,
            )
        )

    if not pages and default_cid:
        pages.append(info.to_track())

    return VideoDetail(info=info, pages=pages)


def normalize_user_profile(data: dict[str, Any]) -> BiliUserProfile:
    return BiliUserProfile(
        mid=int(data.get("mid") or data.get("uid") or 0),
        name=clean_text(data.get("uname") or data.get("name")),
        face=normalize_cover(data.get("face")),
        level=int((data.get("level_info") or {}).get("current_level") or data.get("level") or 0),
        vip_type=int((data.get("vip") or {}).get("type") or 0),
    )


def normalize_favorite_folder(item: dict[str, Any]) -> FavoriteFolder:
    media_id = int(item.get("id") or item.get("media_id") or item.get("mediaId") or 0)
    fid = item.get("fid")
    return FavoriteFolder(
        media_id=media_id,
        fid=int(fid) if fid not in (None, "") else None,
        mid=int(item.get("mid") or 0),
        title=clean_text(item.get("title") or item.get("name")),
        cover=normalize_cover(item.get("cover")),
        media_count=int(item.get("media_count") or item.get("mediaCount") or 0),
        attr=int(item.get("attr") or 0),
        favorite_state=int(item.get("fav_state") or item.get("favoriteState") or 0),
    )


def normalize_favorite_media_item(item: dict[str, Any]) -> Optional[Track]:
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        return None

    upper = item.get("upper") or item.get("owner") or {}
    owner_mid = upper.get("mid")
    cnt_info = item.get("cnt_info") or item.get("stat") or {}
    return Track(
        bvid=bvid,
        title=clean_text(item.get("title")),
        owner=clean_text(upper.get("name")),
        owner_mid=int(owner_mid) if owner_mid not in (None, "") else None,
        cover=normalize_cover(item.get("cover") or item.get("pic")),
        duration=parse_duration(item.get("duration")),
        play_count=int(cnt_info.get("play") or cnt_info.get("view") or 0),
        published_at=format_pubdate(item.get("pubtime") or item.get("pubdate") or item.get("ctime")),
    )


def normalize_video_intro(data: dict[str, Any], cid: Optional[int] = None) -> dict[str, Any]:
    bvid = normalize_bvid(str(data.get("bvid", "")))
    owner = data.get("owner") or {}
    stat = data.get("stat") or {}
    pages = []
    selected_cid = int(cid or data.get("cid") or 0) or None
    for page_item in data.get("pages") or []:
        page_cid = int(page_item.get("cid") or 0)
        pages.append(
            {
                "cid": page_cid,
                "page": int(page_item.get("page") or len(pages) + 1),
                "title": clean_text(page_item.get("part")),
                "duration": parse_duration(page_item.get("duration")),
            }
        )

    return {
        "bvid": bvid,
        "cid": selected_cid,
        "title": clean_text(data.get("title")),
        "description": str(data.get("desc") or "").strip(),
        "dynamic": str(data.get("dynamic") or "").strip(),
        "owner": {
            "mid": int(owner.get("mid") or 0),
            "name": clean_text(owner.get("name")),
            "face": normalize_cover(owner.get("face")),
        },
        "publishedAt": format_pubdate(data.get("pubdate") or data.get("ctime")),
        "stats": {
            "view": int(stat.get("view") or 0),
            "danmaku": int(stat.get("danmaku") or 0),
            "reply": int(stat.get("reply") or 0),
            "favorite": int(stat.get("favorite") or 0),
            "coin": int(stat.get("coin") or 0),
            "share": int(stat.get("share") or 0),
            "like": int(stat.get("like") or 0),
        },
        "pages": pages,
    }


def normalize_player_subtitles(
    player_data: dict[str, Any],
    bvid: str,
    cid: int,
    lines: Optional[list[dict[str, Any]]] = None,
    selected_subtitle_id: Optional[int] = None,
    source_aid: Optional[int] = None,
) -> dict[str, Any]:
    subtitle_data = player_data.get("subtitle") or {}
    subtitles = []
    for item in subtitle_data.get("subtitles") or []:
        subtitle_id = int(item.get("id") or item.get("subtitle_id") or 0)
        subtitle_url = normalize_cover(item.get("subtitle_url") or item.get("subtitleUrl"))
        if not is_valid_subtitle_url(subtitle_url):
            continue
        subtitle = {
            "id": subtitle_id,
            "lan": str(item.get("lan") or ""),
            "lanDoc": str(item.get("lan_doc") or item.get("lanDoc") or ""),
            "url": subtitle_url,
            "authorMid": int(item.get("author_mid") or item.get("authorMid") or 0),
            "type": int(item.get("type") or 0),
            "sourceCid": int(cid),
        }
        if source_aid is not None:
            subtitle["sourceAid"] = int(source_aid)
        subtitles.append(subtitle)

    return {
        "bvid": normalize_bvid(bvid),
        "cid": int(cid),
        "needLogin": bool(player_data.get("need_login_subtitle")),
        "subtitles": subtitles,
        "activeSubtitleId": selected_subtitle_id,
        "lines": lines or [],
    }


def normalize_subtitle_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    lines = []
    body = payload.get("body")
    if not isinstance(body, list):
        return lines
    for item in body:
        if not isinstance(item, dict):
            continue
        if not {"from", "to", "content"}.issubset(item.keys()):
            continue
        try:
            from_time = float(item.get("from") or 0)
            to_time = float(item.get("to") or 0)
        except (TypeError, ValueError):
            continue
        text = clean_text(item.get("content"))
        if not text or not math.isfinite(from_time) or not math.isfinite(to_time) or to_time <= from_time:
            continue
        lines.append(
            {
                "from": from_time,
                "to": to_time,
                "text": text,
            }
        )
    lines.sort(key=lambda line: (line["from"], line["to"]))
    return lines


def normalize_player_chapters(player_data: dict[str, Any], bvid: str, cid: int) -> dict[str, Any]:
    chapters = []
    for item in player_data.get("view_points") or []:
        chapters.append(
            {
                "from": float(item.get("from") or 0),
                "to": float(item.get("to") or 0),
                "title": clean_text(item.get("content") or item.get("title")),
                "cover": normalize_cover(item.get("imgUrl") or item.get("img_url")),
            }
        )
    return {
        "bvid": normalize_bvid(bvid),
        "cid": int(cid),
        "chapters": chapters,
    }


def normalize_reply_comments(
    payload: dict[str, Any],
    bvid: str,
    aid: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    data = payload.get("data") or {}
    cursor = data.get("cursor") or {}
    comments = []
    for item in data.get("replies") or []:
        member = item.get("member") or {}
        content = item.get("content") or {}
        comments.append(
            {
                "id": str(item.get("rpid_str") or item.get("rpid") or ""),
                "author": {
                    "mid": int(member.get("mid") or 0),
                    "name": clean_text(member.get("uname")),
                    "avatar": normalize_cover(member.get("avatar")),
                },
                "message": clean_text(content.get("message")),
                "like": int(item.get("like") or 0),
                "replyCount": int(item.get("rcount") or 0),
                "createdAt": format_pubdate(item.get("ctime")),
            }
        )

    return {
        "bvid": normalize_bvid(bvid),
        "aid": int(aid),
        "page": page,
        "pageSize": page_size,
        "total": int(cursor.get("all_count") or 0),
        "hasMore": not bool(cursor.get("is_end")),
        "comments": comments,
    }


def normalize_space_profile(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "mid": int(data.get("mid") or 0),
        "name": clean_text(data.get("name")),
        "face": normalize_cover(data.get("face")),
        "sign": str(data.get("sign") or "").strip(),
        "level": int((data.get("level_info") or {}).get("current_level") or data.get("level") or 0),
    }


def normalize_space_archive_item(item: dict[str, Any], owner: dict[str, Any]) -> Optional[Track]:
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        return None
    stat = item.get("stat") or {}
    return Track(
        bvid=bvid,
        title=clean_text(item.get("title")),
        owner=clean_text(owner.get("name")),
        owner_mid=int(owner.get("mid") or 0) or None,
        cover=normalize_cover(item.get("pic")),
        duration=parse_duration(item.get("duration") or item.get("length")),
        play_count=int(stat.get("view") or item.get("play") or 0),
        published_at=format_pubdate(item.get("pubdate") or item.get("created")),
    )


def cover_info_from_video_data(data: dict[str, Any], cid: Optional[int] = None) -> dict[str, Any]:
    bvid = normalize_bvid(str(data.get("bvid", "")))
    video_cover = normalize_cover(data.get("pic"))
    owner_face = normalize_cover((data.get("owner") or {}).get("face"))

    pages = []
    selected_page = None
    for page_item in data.get("pages") or []:
        page_cid = int(page_item.get("cid") or 0)
        page_payload = {
            "cid": page_cid,
            "page": int(page_item.get("page") or len(pages) + 1),
            "pageTitle": clean_text(page_item.get("part")),
            "cover": video_cover,
            "firstFrame": normalize_cover(page_item.get("first_frame")),
        }
        pages.append(page_payload)
        if cid is not None and page_cid == int(cid):
            selected_page = page_payload

    selected_cover = video_cover
    selected_first_frame = None
    if selected_page:
        selected_first_frame = selected_page.get("firstFrame")
        selected_cover = selected_first_frame or video_cover

    return {
        "bvid": bvid,
        "cid": cid,
        "cover": selected_cover,
        "videoCover": video_cover,
        "pageCover": selected_first_frame,
        "ownerFace": owner_face,
        "pages": pages,
    }
