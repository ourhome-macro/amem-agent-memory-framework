from bili_client import BiliClient, BilibiliAPI
from models import AudioStreamInfo, VideoDetail, VideoInfo


bilibili_api = BiliClient()

__all__ = [
    "AudioStreamInfo",
    "BiliClient",
    "BilibiliAPI",
    "VideoDetail",
    "VideoInfo",
    "bilibili_api",
]
