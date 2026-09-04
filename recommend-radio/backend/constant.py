import re


class HttpHeader:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ACCEPT = "application/json, text/plain, */*"
    ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
    ACCEPT_RANGES = "bytes"

    @classmethod
    def default_headers(cls) -> dict:
        return {
            "User-Agent": cls.USER_AGENT,
            "Referer": "https://www.bilibili.com",
            "Accept": cls.ACCEPT,
            "Accept-Language": cls.ACCEPT_LANGUAGE,
        }

    @classmethod
    def video_headers(cls, bvid: str) -> dict:
        return {
            **cls.default_headers(),
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }

    @classmethod
    def search_headers(cls) -> dict:
        return {
            **cls.default_headers(),
            "Referer": "https://search.bilibili.com/",
            "Origin": "https://search.bilibili.com",
        }

    @classmethod
    def stream_headers(cls, bvid: str) -> dict:
        return {
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "User-Agent": cls.USER_AGENT,
        }


class BilibiliAPI:
    VIDEO_INFO_URL = "https://api.bilibili.com/x/web-interface/view"
    PLAY_URL = "https://api.bilibili.com/x/player/playurl"
    PLAYER_INFO_URL = "https://api.bilibili.com/x/player/wbi/v2"
    REPLY_MAIN_URL = "https://api.bilibili.com/x/v2/reply/main"
    NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
    QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    FAVORITE_FOLDERS_URL = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
    FAVORITE_RESOURCE_URL = "https://api.bilibili.com/x/v3/fav/resource/list"
    SPACE_INFO_URL = "https://api.bilibili.com/x/space/wbi/acc/info"
    SPACE_ARCHIVE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"

    BV_PATTERN = re.compile(r"^(BV|bv)[0-9A-Za-z]{10}$")
    URL_PATTERN = re.compile(
        r"^(https?://)?(www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})"
    )


class Server:
    HOST = "0.0.0.0"
    PORT = 5000
    DEBUG = False

    @classmethod
    def proxy_url(cls, bvid: str) -> str:
        return f"http://localhost:{cls.PORT}/api/stream/{bvid}"


class Stream:
    CHUNK_SIZE = 8192
    TIMEOUT = 30
    BUFFER_SIZE = 10 * 1024 * 1024
    LOW_WATERMARK = 0.3
    HIGH_WATERMARK = 0.8
