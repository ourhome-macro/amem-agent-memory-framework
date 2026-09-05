from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORD_POOL_VERSION = "music-keyword-pool-2026-09-03"


@dataclass(frozen=True)
class KeywordEntry:
    name: str
    aliases: tuple[str, ...]
    kind: str


TOPIC_KEYWORDS: tuple[KeywordEntry, ...] = (
    KeywordEntry("R&B", ("rnb", "r&b", "节奏布鲁斯"), "genre"),
    KeywordEntry("抒情", ("抒情", "情歌", "慢歌", "ballad"), "mood"),
    KeywordEntry("华语流行", ("华语流行", "中文歌", "国语", "华语"), "genre"),
    KeywordEntry("粤语", ("粤语", "港乐"), "language"),
    KeywordEntry("J-Pop", ("j-pop", "jpop", "日语", "日文歌", "日系"), "language"),
    KeywordEntry("K-Pop", ("k-pop", "kpop", "韩语", "韩文歌"), "language"),
    KeywordEntry("Vocaloid", ("vocaloid", "术力口", "初音"), "culture"),
    KeywordEntry("ACG", ("acg", "动漫", "番剧", "op", "ed"), "culture"),
    KeywordEntry("Rap", ("rap", "说唱", "嘻哈", "hiphop", "hip-hop"), "genre"),
    KeywordEntry("雷鬼", ("雷鬼", "reggae"), "genre"),
    KeywordEntry("摇滚", ("摇滚", "rock"), "genre"),
    KeywordEntry("电子", ("电子", "edm"), "genre"),
    KeywordEntry("纯音乐", ("纯音乐", "instrumental", "钢琴", "吉他", "古典"), "genre"),
    KeywordEntry("治愈系", ("治愈", "温柔", "舒缓", "放松"), "mood"),
    KeywordEntry("Lo-fi", ("lo-fi", "lofi", "lo fi"), "genre"),
)

ARTIST_KEYWORDS: tuple[KeywordEntry, ...] = (
    KeywordEntry("周杰伦", ("周杰伦", "jay chou", "jaychou"), "artist"),
    KeywordEntry("陈奕迅", ("陈奕迅", "eason chan", "eason"), "artist"),
    KeywordEntry("孙燕姿", ("孙燕姿", "stefanie sun"), "artist"),
    KeywordEntry("林俊杰", ("林俊杰", "jj lin"), "artist"),
    KeywordEntry("王力宏", ("王力宏", "leehom"), "artist"),
    KeywordEntry("陶喆", ("陶喆", "david tao"), "artist"),
    KeywordEntry("方大同", ("方大同", "khalil fong"), "artist"),
    KeywordEntry("五月天", ("五月天", "mayday"), "artist"),
    KeywordEntry("王菲", ("王菲", "faye wong"), "artist"),
    KeywordEntry("张惠妹", ("张惠妹", "a-mei", "amei"), "artist"),
    KeywordEntry("蔡依林", ("蔡依林", "jolin"), "artist"),
    KeywordEntry("邓紫棋", ("邓紫棋", "gem", "g.e.m"), "artist"),
    KeywordEntry("薛之谦", ("薛之谦",), "artist"),
    KeywordEntry("毛不易", ("毛不易",), "artist"),
    KeywordEntry("李荣浩", ("李荣浩",), "artist"),
    KeywordEntry("田馥甄", ("田馥甄", "hebe"), "artist"),
    KeywordEntry("梁静茹", ("梁静茹",), "artist"),
    KeywordEntry("张学友", ("张学友", "jacky cheung"), "artist"),
    KeywordEntry("告五人", ("告五人", "accusefive"), "artist"),
)

POSITIVE_INTENT_WORDS = (
    "喜欢",
    "爱听",
    "想听",
    "多推",
    "偏好",
    "感兴趣",
    "继续听",
)
NEGATIVE_INTENT_WORDS = (
    "不喜欢",
    "不爱听",
    "不想听",
    "讨厌",
    "少推",
    "别推",
    "不要推",
    "避开",
)
RECOMMENDATION_WORDS = (
    "推荐",
    "推几首",
    "来几首",
    "来点",
    "放几首",
    "听什么",
    "歌单",
    "想听",
)
RECALL_WORDS = (
    "之前",
    "以前",
    "上次",
    "听过",
    "貌似听过",
    "找回",
    "记得",
)
EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "放松": ("累", "疲惫", "压力", "烦", "焦虑", "放空", "缓一缓"),
    "安静": ("失眠", "睡不着", "夜深", "安静", "轻一点"),
    "开心": ("开心", "高兴", "兴奋", "来劲", "提神"),
    "难过": ("难过", "低落", "emo", "沮丧", "不舒服"),
}

MUSIC_RELEVANCE_WORDS = (
    "音乐",
    "歌曲",
    "歌",
    "听歌",
    "唱歌",
    "演唱",
    "演奏",
    "现场",
    "live",
    "翻唱",
    "cover",
    "mv",
    "ost",
    "op",
    "ed",
    "专辑",
    "单曲",
    "作词",
    "作曲",
    "编曲",
    "歌词",
    "旋律",
    "人声",
    "伴奏",
    "钢琴",
    "吉他",
    "贝斯",
    "鼓点",
    "rnb",
    "r&b",
    "ballad",
    "rap",
    "hiphop",
    "hip-hop",
    "雷鬼",
    "reggae",
    "摇滚",
    "流行",
    "民谣",
    "电子",
    "纯音乐",
    "vocaloid",
    "术力口",
    "lo-fi",
    "lofi",
    "j-pop",
    "k-pop",
)
GOSSIP_EXCLUSION_WORDS = (
    "八卦",
    "绯闻",
    "恋情曝光",
    "出轨",
    "离婚",
    "塌房",
    "爆料",
    "狗仔",
    "娱记",
    "吃瓜",
    "瓜主",
    "私生活",
    "粉丝互撕",
    "粉丝开撕",
    "饭圈大战",
    "机场路透",
    "红毯生图",
    "代言翻车",
)
NON_MUSIC_CONTEXT_WORDS = (
    "采访",
    "综艺",
    "花絮",
    "预告",
    "reaction",
    "解说",
    "影视剪辑",
    "电视剧",
    "电影",
    "游戏实况",
    "开箱",
    "美妆",
    "vlog",
)


def match_topics(text: str) -> list[str]:
    normalized = text.casefold()
    result: list[str] = []
    for entry in TOPIC_KEYWORDS:
        if any(_contains_keyword(normalized, alias) for alias in entry.aliases):
            result.append(entry.name)
    return result


def matched_artist_names(text: str) -> list[str]:
    normalized = text.casefold()
    result: list[str] = []
    for entry in ARTIST_KEYWORDS:
        if any(_contains_keyword(normalized, alias) for alias in entry.aliases):
            result.append(entry.name)
    return result


def topic_phrase(text: str) -> str:
    topics = match_topics(text)
    if "抒情" in topics and "R&B" in topics:
        return "抒情 R&B"
    return "、".join(topics[:3])


def has_positive_intent(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in POSITIVE_INTENT_WORDS)


def has_negative_intent(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in NEGATIVE_INTENT_WORDS)


def is_recommendation_request(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in RECOMMENDATION_WORDS)


def is_recall_request(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in RECALL_WORDS)


def detect_emotion(text: str) -> str:
    normalized = text.casefold()
    for mood, aliases in EMOTION_KEYWORDS.items():
        if any(_contains_keyword(normalized, alias) for alias in aliases):
            return mood
    return ""


def has_gossip_exclusion(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in GOSSIP_EXCLUSION_WORDS)


def has_music_relevance_signal(text: str) -> bool:
    normalized = text.casefold()
    if match_topics(normalized):
        return True
    return any(_contains_keyword(normalized, word) for word in MUSIC_RELEVANCE_WORDS)


def has_non_music_context(text: str) -> bool:
    normalized = text.casefold()
    return any(_contains_keyword(normalized, word) for word in NON_MUSIC_CONTEXT_WORDS)


def is_music_relevant(text: str) -> bool:
    if has_gossip_exclusion(text):
        return False
    if has_music_relevance_signal(text):
        return True
    return not has_non_music_context(text)


def _contains_keyword(text: str, keyword: str) -> bool:
    normalized = text.casefold()
    term = keyword.casefold().strip()
    if not term:
        return False
    if term.isascii() and any(char.isalnum() for char in term):
        pattern = r"\s+".join(re.escape(part) for part in term.split())
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", normalized) is not None
    return term in normalized
