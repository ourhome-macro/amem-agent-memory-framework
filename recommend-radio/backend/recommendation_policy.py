from __future__ import annotations

import random
from datetime import datetime, timezone
from music_keyword_pool import (
    has_gossip_exclusion,
    has_music_relevance_signal,
    has_non_music_context,
    is_music_relevant,
)
from music_profile import MusicProfile
from request_spec import RequestSpec
from recommendation_contracts import (
    CandidateDraft,
    EXPLORE_SLOT_COUNT,
    EXPLORE_SOURCES,
    HIGH_SCORE_SLOT_COUNT,
    NEGATIVE_OWNER_SCORE_PENALTY,
    RecommendationCandidate,
    SEARCH_BACKED_SOURCES,
    SERVICE_DEFAULT_SAME_ARTIST_LIMIT,
    SERVICE_DEFAULT_SAME_UPLOADER_LIMIT,
    UserProfile,
    _add_score_signal,
    _candidate_artist_keys,
    _candidate_owner_mid,
    _candidate_reason,
    _candidate_text,
    _clean_agent_reason,
    _matched_profile_topics,
    _profile_signal_names,
    _uploader_key,
)


class RecommendationPolicy:
    def __init__(self, user_id: str):
        self.user_id = user_id

    def validate_and_finalize(
        self,
        candidates: list[RecommendationCandidate],
        *,
        profile: MusicProfile,
        legacy_profile: UserProfile,
        limit: int,
        scene: str,
        request_spec: RequestSpec | None = None,
    ) -> list[RecommendationCandidate]:
        resolved_request_spec = request_spec or RequestSpec()
        filtered = self._filter_candidates(
            candidates,
            profile=profile,
            legacy_profile=legacy_profile,
        )
        filtered = [item for item in filtered if resolved_request_spec.matches_facets(item.facets)]
        selected = self._select_epsilon_greedy(filtered, limit, scene, legacy_profile, profile)
        return self._apply_diversity_limits(
            selected,
            profile.same_uploader_limit,
            limit,
            request_scoped_limit=None if resolved_request_spec.constrained else 2,
        )

    def _score_candidate(
        self,
        draft: CandidateDraft,
        profile: UserProfile,
        music_profile: MusicProfile,
        agent_trace_id: str,
        request_spec: RequestSpec | None = None,
    ) -> RecommendationCandidate:
        track = draft.track
        score = 0.0
        score_signals: dict[str, float] = {}
        matched_preferences: list[str] = []
        evidence: list[str] = []
        penalties: list[str] = []
        text = (
            f"{track.title} {track.owner} {' '.join(draft.tags)} {' '.join(draft.profile_signals)}"
        )

        if track.owner_mid and track.owner_mid in profile.frequent_owner_mids:
            score += _add_score_signal(score_signals, "frequent_owner", 3)
            evidence.append("来自你最近常听的 UP 或相近来源")

        matched_tags = sorted(draft.tags & profile.common_tags)
        if matched_tags:
            score += _add_score_signal(score_signals, "tag_match", 3)
            matched_preferences.extend(matched_tags[:2])
            evidence.append(f"命中你近期标过的标签：{'、'.join(matched_tags[:2])}")

        if track.owner_mid and track.owner_mid in profile.repeated_owner_mids:
            score += _add_score_signal(score_signals, "recent_owner_repeat", 2)
            evidence.append("最近重复听过相近来源")

        if track.owner_mid and track.owner_mid in profile.completed_owner_mids:
            score += _add_score_signal(score_signals, "recent_completion", 2)
            evidence.append("最近完整听完过相近来源")

        positive_weight = music_profile.topic_weight(text, positive=True)
        if positive_weight:
            score += _add_score_signal(score_signals, "profile_match", 4 * positive_weight)
            profile_topics = _matched_profile_topics(text, music_profile.positive_topics)
            matched_preferences.extend(profile_topics)
            if profile_topics:
                evidence.append(f"贴近你稳定偏好的 {'、'.join(profile_topics[:3])}")
            else:
                evidence.append("贴近你稳定偏好的听感")

        negative_weight = music_profile.topic_weight(text, positive=False)
        if set((request_spec or RequestSpec()).required_genres) & set(
            draft.facets.get("genres") or []
        ):
            negative_weight = 0.0
        if negative_weight:
            score += _add_score_signal(
                score_signals, "negative_preference_penalty", -3 * negative_weight
            )
            negative_topics = _matched_profile_topics(text, music_profile.negative_topics)
            if negative_topics:
                penalties.append(f"命中你回避的 {'、'.join(negative_topics[:2])}")
            else:
                penalties.append("命中近期负反馈方向")

        uploader_key = _uploader_key(track)
        uploader_weight = music_profile.uploader_weight(uploader_key)
        if uploader_weight:
            score += _add_score_signal(score_signals, "preferred_uploader", 3 * uploader_weight)
            evidence.append("来自你更容易接受的 UP 或来源")

        if track.track_id in profile.recently_heard_track_ids:
            score += _add_score_signal(score_signals, "recently_heard_penalty", -3)
            penalties.append("最近已经听过")
        if track.track_id in profile.recently_recommended_track_ids:
            score += _add_score_signal(score_signals, "fatigue_penalty", -4)
            penalties.append("近期已经推荐过")
        if track.track_id in profile.skipped_track_ids:
            score += _add_score_signal(score_signals, "skip_penalty", -5)
            penalties.append("你之前跳过或点过不感兴趣")
        if track.owner_mid and track.owner_mid in profile.negative_owner_mids:
            score += _add_score_signal(
                score_signals,
                "negative_owner_penalty",
                -NEGATIVE_OWNER_SCORE_PENALTY,
            )
            penalties.append("该 UP 近期负反馈较多")

        if (request_spec or RequestSpec()).constrained and draft.scope_evidence:
            evidence.append("满足本轮范围约束")
        if "discovery_search" in draft.sources and draft.llm_reason:
            matched_preferences.extend(_profile_signal_names(draft.profile_signals))
            evidence.append(f"搜索计划命中：{_clean_agent_reason(draft.llm_reason)}")
        elif "tag_search" in draft.sources and matched_tags:
            evidence.append(f"按 {matched_tags[0]} 扩展探索")
        elif "popular_music" in draft.sources and score <= 0:
            evidence.append("来自最近热门音乐候选")

        if not evidence:
            evidence.append(self._source_reason(draft.sources))

        return RecommendationCandidate(
            track=track.to_dict(),
            score=score,
            source=self._primary_source(draft.sources),
            reason=_candidate_reason(evidence, penalties),
            llm_reason=draft.llm_reason,
            profile_signals=draft.profile_signals,
            agent_trace_id=agent_trace_id if "discovery_search" in draft.sources else None,
            tags=sorted(draft.tags),
            score_signals=score_signals,
            matched_preferences=list(dict.fromkeys(matched_preferences)),
            evidence=list(dict.fromkeys(evidence)),
            penalties=list(dict.fromkeys(penalties)),
            facets=draft.facets,
            scope_evidence=draft.scope_evidence,
            scope_kind=draft.scope_kind,
            source_keyword_ids=sorted(draft.source_keyword_ids),
            source_keyword_family_ids=sorted(draft.source_keyword_family_ids),
            source_discovery_job_ids=sorted(draft.source_discovery_job_ids),
            text_embedding=list(draft.text_embedding),
            audio_embedding=list(draft.audio_embedding),
        )

    def _select_epsilon_greedy(
        self,
        candidates: list[RecommendationCandidate],
        limit: int,
        scene: str,
        profile: UserProfile,
        music_profile: MusicProfile,
    ) -> list[RecommendationCandidate]:
        if not candidates:
            return []

        if music_profile.exploration_ratio > 0:
            explore_count = min(limit, max(0, round(limit * music_profile.exploration_ratio)))
        else:
            explore_count = min(EXPLORE_SLOT_COUNT, max(limit - HIGH_SCORE_SLOT_COUNT, 0))
        high_count = limit - explore_count
        selected_ids: set[str] = set()
        explore_selected: list[RecommendationCandidate] = []
        high_selected: list[RecommendationCandidate] = []

        explore_pool = [
            item
            for item in candidates
            if self._is_unfamiliar(item, profile) and item.source in EXPLORE_SOURCES
        ][:50]

        seed = f"{self.user_id}:{scene}:{datetime.now(timezone.utc).date().isoformat()}"
        rng = random.Random(seed)
        rng.shuffle(explore_pool)

        for item in explore_pool[:explore_count]:
            item.source = "explore"
            if not item.reason.startswith("探索"):
                item.reason = f"探索：{item.reason}"
            explore_selected.append(item)
            selected_ids.add(item.track["trackId"])

        high_pool = [
            item
            for item in candidates
            if item.track["trackId"] not in selected_ids
            and not self._is_skipped(item, profile)
            and item.track["trackId"] not in profile.recently_recommended_track_ids
        ]
        for item in high_pool[:high_count]:
            high_selected.append(item)
            selected_ids.add(item.track["trackId"])

        if len(high_selected) < high_count:
            for item in candidates:
                track_id = item.track["trackId"]
                if (
                    track_id in selected_ids
                    or self._is_skipped(item, profile)
                    or track_id in profile.recently_recommended_track_ids
                ):
                    continue
                high_selected.append(item)
                selected_ids.add(track_id)
                if len(high_selected) >= high_count:
                    break

        return (high_selected + explore_selected)[:limit]

    @staticmethod
    def _apply_diversity_limits(
        candidates: list[RecommendationCandidate],
        same_uploader_limit: int,
        limit: int,
        *,
        request_scoped_limit: int | None = 2,
    ) -> list[RecommendationCandidate]:
        uploader_limit = (
            same_uploader_limit if same_uploader_limit > 0 else SERVICE_DEFAULT_SAME_UPLOADER_LIMIT
        )
        uploader_counts: dict[str, int] = {}
        artist_counts: dict[str, int] = {}
        selected_work_ids: set[str] = set()
        contextual_count = 0
        selected: list[RecommendationCandidate] = []
        for item in candidates:
            uploader = str(item.track.get("ownerMid") or item.track.get("owner") or "")
            artist_keys = _candidate_artist_keys(item)
            work_id = str(item.track.get("workId") or "")
            if work_id and work_id in selected_work_ids:
                continue
            if uploader and uploader_counts.get(uploader, 0) >= uploader_limit:
                continue
            if any(
                artist_counts.get(artist, 0) >= SERVICE_DEFAULT_SAME_ARTIST_LIMIT
                for artist in artist_keys
            ):
                continue
            if (
                request_scoped_limit is not None
                and item.scope_kind == "request"
                and contextual_count >= request_scoped_limit
            ):
                continue
            selected.append(item)
            if uploader:
                uploader_counts[uploader] = uploader_counts.get(uploader, 0) + 1
            for artist in artist_keys:
                artist_counts[artist] = artist_counts.get(artist, 0) + 1
            if work_id:
                selected_work_ids.add(work_id)
            if item.scope_kind == "request":
                contextual_count += 1
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _filter_candidates(
        candidates: list[RecommendationCandidate],
        *,
        profile: MusicProfile,
        legacy_profile: UserProfile,
    ) -> list[RecommendationCandidate]:
        return [
            item
            for item in candidates
            if not RecommendationPolicy._is_hard_filtered(item, profile, legacy_profile)
        ]

    @staticmethod
    def _is_hard_filtered(
        item: RecommendationCandidate,
        profile: MusicProfile,
        legacy_profile: UserProfile,
    ) -> bool:
        track_id = str(item.track.get("trackId") or "")
        if track_id in legacy_profile.skipped_track_ids:
            return True
        recording_id = str(item.track.get("recordingId") or "")
        if recording_id and recording_id in legacy_profile.skipped_recording_ids:
            return True
        if not RecommendationPolicy._is_music_candidate(item, legacy_profile):
            return True
        owner_mid = _candidate_owner_mid(item)
        trusted_owner_mids = (
            set(getattr(legacy_profile, "frequent_owner_mids", set()))
            | set(getattr(legacy_profile, "liked_owner_mids", set()))
            | set(getattr(legacy_profile, "completed_owner_mids", set()))
        )
        if (
            owner_mid in set(getattr(legacy_profile, "negative_owner_mids", set()))
            and owner_mid not in trusted_owner_mids
        ):
            return True
        uploader = str(item.track.get("ownerMid") or item.track.get("owner") or "")
        return profile.hard_blocked_uploader(uploader) or profile.avoided_uploader(uploader)

    @staticmethod
    def _is_music_candidate(item: RecommendationCandidate, profile: UserProfile) -> bool:
        text = _candidate_text(item)
        if has_gossip_exclusion(text):
            return False
        if item.source in SEARCH_BACKED_SOURCES:
            if has_non_music_context(text) and not has_music_relevance_signal(text):
                return False
            return has_music_relevance_signal(text) or bool(item.facets.get("genres"))
        if has_non_music_context(text) and not has_music_relevance_signal(text):
            return False
        owner_mid = _candidate_owner_mid(item)
        trusted_owner_mids = (
            set(getattr(profile, "frequent_owner_mids", set()))
            | set(getattr(profile, "liked_owner_mids", set()))
            | set(getattr(profile, "completed_owner_mids", set()))
        )
        if owner_mid in trusted_owner_mids:
            return True
        return is_music_relevant(text)

    @staticmethod
    def _is_unfamiliar(item: RecommendationCandidate, profile: UserProfile) -> bool:
        track_id = item.track["trackId"]
        recording_id = str(item.track.get("recordingId") or "")
        work_id = str(item.track.get("workId") or "")
        return (
            track_id not in profile.recently_heard_track_ids
            and track_id not in profile.recently_recommended_track_ids
            and track_id not in profile.skipped_track_ids
            and (not recording_id or recording_id not in profile.recently_heard_recording_ids)
            and (not work_id or work_id not in profile.recently_recommended_work_ids)
        )

    @staticmethod
    def _is_skipped(item: RecommendationCandidate, profile: UserProfile) -> bool:
        recording_id = str(item.track.get("recordingId") or "")
        return item.track["trackId"] in profile.skipped_track_ids or (
            bool(recording_id) and recording_id in profile.skipped_recording_ids
        )

    @staticmethod
    def _primary_source(sources: set[str]) -> str:
        for source in [
            "discovery_search",
            "frequent_up",
            "liked_up",
            "tag_search",
            "tag_match",
            "popular_music",
            "library",
        ]:
            if source in sources:
                return source
        return next(iter(sources), "library")

    @staticmethod
    def _source_reason(sources: set[str]) -> str:
        source = RecommendationPolicy._primary_source(sources)
        return {
            "discovery_search": "候选池中的发现结果",
            "frequent_up": "常听 UP 的其他稿件",
            "liked_up": "喜欢歌曲 UP 的其他稿件",
            "tag_search": "同标签搜索结果",
            "tag_match": "标签相同的歌曲",
            "popular_music": "最近热门音乐稿件",
        }.get(source, "来自你的播放和收藏记录")
