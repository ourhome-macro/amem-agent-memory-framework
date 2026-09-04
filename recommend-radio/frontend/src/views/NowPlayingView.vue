<template>
  <div class="now-playing">
    <!-- 品牌氛围背景：模糊封面单独分层，容器本身不加 filter -->
    <div class="ambient" :class="{ still: reducedMotion || !isPlaying }">
      <div
        v-for="n in 3"
        :key="n"
        class="ambient-layer"
        :class="'layer-' + n"
        :style="coverStyle"
      />
    </div>
    <div class="ambient-mask" />

    <!-- 顶部栏 -->
    <header class="np-header">
      <button class="np-icon-btn" title="收起" @click="ui.closeNowPlaying()">
        <AppIcon name="chevron-down" :size="22" />
      </button>
      <span class="np-brand">正在播放</span>
      <div class="np-spacer" />
    </header>

    <div class="np-body">
      <!-- 左侧：黑胶 + 唱针 -->
      <div class="disc-side">
        <div class="tonearm" :class="{ playing: isPlaying }">
          <svg viewBox="0 0 60 120" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="8" fill="#3a3a3f" />
            <circle cx="12" cy="12" r="3" fill="#5a5a60" />
            <rect x="9" y="12" width="6" height="70" rx="3" fill="#48484e" transform="rotate(-18 12 12)" />
            <rect x="30" y="72" width="14" height="20" rx="3" fill="#3a3a3f" transform="rotate(-18 12 12)" />
          </svg>
        </div>

        <div class="vinyl" :class="{ spinning: isPlaying, paused: !isPlaying }">
          <div class="vinyl-disc">
            <div class="vinyl-grooves" />
            <div class="vinyl-label">
              <img v-if="track?.cover" :src="mediaUrl(track.cover)" :alt="track?.title" />
              <div v-else class="label-fallback">
                <AppIcon name="disc" :size="40" />
              </div>
            </div>
            <div class="vinyl-hole" />
          </div>
        </div>
      </div>

      <!-- 右侧：信息区 -->
      <div class="info-side">
        <div class="track-head">
          <h1 class="np-title" :title="track?.title">{{ track?.title || '未在播放' }}</h1>
          <button
            v-if="track?.bvid"
            class="np-owner np-owner-link"
            type="button"
            :title="'打开 UP 主页：' + track.owner"
            @click="openOwner"
          >
            {{ ownerLine }}
          </button>
          <p v-else class="np-owner">{{ ownerLine }}</p>
          <p class="np-stats">{{ statsLine }}</p>
          <div class="detail-actions">
            <button class="detail-btn" :disabled="!track" title="加入播放队列" @click="enqueueCurrent">
              <AppIcon name="plus" :size="16" />
              <span>加入队列</span>
            </button>
            <button class="detail-btn" :disabled="!track" title="添加到歌单" @click="playlistMenuOpen = !playlistMenuOpen">
              <AppIcon name="list" :size="16" />
              <span>添加到歌单</span>
            </button>
            <button
              class="detail-btn"
              :title="isDownloading ? '下载中...' : '下载当前音频'"
              :disabled="!track || isDownloading"
              @click="player.downloadCurrent()"
            >
              <AppIcon name="download" :size="16" :class="{ 'spin-slow': isDownloading }" />
              <span>{{ isDownloading ? '下载中' : '下载' }}</span>
            </button>
          </div>
          <div v-if="playlistMenuOpen" class="playlist-menu">
            <button
              v-for="playlist in library.playlists"
              :key="playlist.id"
              class="playlist-option"
              :disabled="!track || library.hasPlaylistTrack(playlist.id, track)"
              @click="addCurrentToPlaylist(playlist.id)"
            >
              <AppIcon name="list" :size="14" />
              <span>{{ playlist.name }}</span>
              <small v-if="track && library.hasPlaylistTrack(playlist.id, track)">已存在</small>
            </button>
            <p v-if="library.playlists.length === 0" class="playlist-empty">先在侧边栏新建歌单</p>
          </div>
        </div>

        <div class="info-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            class="tab-btn"
            :class="{ active: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>

        <div ref="infoPanelRef" class="info-panel" @scroll="handleInfoPanelScroll">
          <template v-if="activeTab === 'intro'">
            <div v-if="introLoading" class="panel-text muted">正在读取简介...</div>
            <button v-else-if="introError" class="panel-error" @click="loadActiveTab">{{ introError }}</button>
            <div v-else-if="intro" class="intro-panel">
              <div class="intro-stats">
                <span>{{ formatCount(intro.stats.view) }}播放</span>
                <span>{{ formatCount(intro.stats.like) }}赞</span>
                <span>{{ formatCount(intro.stats.reply) }}评论</span>
                <span>{{ formatCount(intro.stats.favorite) }}收藏</span>
              </div>
              <p v-if="intro.description" class="panel-text intro-text">{{ intro.description }}</p>
              <p v-else class="panel-text muted">UP 主暂未填写简介。</p>
              <p v-if="intro.dynamic" class="panel-text dynamic-text">{{ intro.dynamic }}</p>
            </div>
            <p v-else class="panel-text muted">暂无简介。</p>
          </template>
          <template v-else-if="activeTab === 'subtitle'">
            <div v-if="subtitleLoading" class="panel-text muted">正在读取字幕...</div>
            <button v-else-if="subtitleError" class="panel-error" @click="loadActiveTab">{{ subtitleError }}</button>
            <div v-else-if="subtitles" class="subtitle-panel">
              <div v-if="subtitles.subtitles.length" class="subtitle-meta">
                <span
                  v-for="item in subtitles.subtitles"
                  :key="item.id"
                  :class="{ active: item.id === subtitles.activeSubtitleId }"
                >
                  {{ item.lanDoc || item.lan || '字幕' }}
                </span>
              </div>
              <ol v-if="subtitles.lines.length" class="subtitle-lines">
                <li
                  v-for="(line, index) in subtitles.lines"
                  :key="line.from + '-' + line.to + '-' + line.text"
                  :data-subtitle-index="index"
                  :class="{ active: index === activeSubtitleLineIndex }"
                >
                  <button type="button" class="subtitle-line-button" @click="player.seek(line.from)">
                    <span class="time-badge">{{ formatTimeLabel(line.from) }}</span>
                    <span>{{ line.text }}</span>
                  </button>
                </li>
              </ol>
              <p v-else class="panel-text muted">
                {{ subtitles.needLogin ? '该视频字幕需要登录后读取。' : 'B站暂未提供字幕。' }}
              </p>
            </div>
            <p v-else class="panel-text muted">暂无字幕。</p>
          </template>
          <template v-else-if="activeTab === 'chapters'">
            <div v-if="chaptersLoading" class="panel-text muted">正在读取章节...</div>
            <button v-else-if="chaptersError" class="panel-error" @click="loadActiveTab">{{ chaptersError }}</button>
            <div v-else-if="chapters?.chapters.length" class="chapter-list">
              <button
                v-for="chapter in chapters.chapters"
                :key="chapter.from + '-' + chapter.title"
                class="chapter-item"
                @click="player.seek(chapter.from)"
              >
                <span class="time-badge">{{ formatTimeLabel(chapter.from) }}</span>
                <span>{{ chapter.title }}</span>
              </button>
            </div>
            <p v-else class="panel-text muted">B站暂未提供章节。</p>
          </template>
          <template v-else-if="activeTab === 'comments'">
            <div v-if="commentsLoading" class="panel-text muted">正在读取评论区...</div>
            <button v-else-if="commentsError" class="panel-error" @click="loadActiveTab">{{ commentsError }}</button>
            <div v-else-if="comments?.comments.length" class="comment-list">
              <article v-for="comment in comments.comments" :key="comment.id" class="comment-item">
                <img v-if="comment.author.avatar" :src="mediaUrl(comment.author.avatar)" :alt="comment.author.name" />
                <div v-else class="comment-avatar">{{ comment.author.name.slice(0, 1) || '评' }}</div>
                <div class="comment-body">
                  <div class="comment-head">
                    <span>{{ comment.author.name }}</span>
                    <small>{{ formatCount(comment.like) }}赞</small>
                  </div>
                  <p>{{ comment.message }}</p>
                </div>
              </article>
              <div v-if="commentsLoadingMore" class="panel-more-loading">
                <LoadingDots />
                <span>正在加载更多评论</span>
              </div>
              <button v-else-if="commentsMoreError" class="panel-more-btn error" type="button" @click="loadMoreComments(true)">
                {{ commentsMoreError }}
              </button>
              <button v-else-if="commentsHasMore" class="panel-more-btn" type="button" @click="loadMoreComments()">
                加载更多评论
              </button>
              <span v-else-if="comments.comments.length >= commentsPageSize" class="panel-end">
                没有更多评论了
              </span>
            </div>
            <p v-else class="panel-text muted">暂未读取到评论。</p>
          </template>
          <template v-else>
            <div class="review-panel" aria-label="私人评价">
              <div class="review-head">
                <div>
                  <h2>私人评价</h2>
                  <p v-if="reviewUpdatedAt">{{ reviewUpdatedAt }}</p>
                </div>
                <span class="review-private">仅自己可见</span>
              </div>

              <div class="review-stars" role="radiogroup" aria-label="星级">
                <button
                  v-for="star in 5"
                  :key="star"
                  type="button"
                  class="review-star"
                  :class="{ active: star <= reviewRating }"
                  :disabled="!track || reviewSaving"
                  :aria-label="String(star) + ' star'"
                  @click="reviewRating = star"
                >
                  <AppIcon :name="star <= reviewRating ? 'star-filled' : 'star'" :size="18" />
                </button>
              </div>

              <div class="review-moods" aria-label="标签">
                <button
                  v-for="mood in reviewMoods"
                  :key="mood"
                  type="button"
                  class="review-mood"
                  :class="{ active: reviewMood === mood }"
                  :disabled="!track || reviewSaving"
                  @click="selectReviewLabel(mood)"
                >
                  {{ mood }}
                </button>
              </div>

              <div class="review-custom-label">
                <input
                  v-model="reviewCustomLabel"
                  :disabled="!track || reviewSaving"
                  maxlength="4"
                  placeholder="自定义标签"
                  @input="syncCustomLabel"
                />
                <button
                  type="button"
                  class="review-action ghost"
                  :disabled="!canApplyCustomLabel"
                  @click="applyCustomLabel"
                >
                  添加
                </button>
              </div>

              <textarea
                v-model="reviewNote"
                class="review-note"
                :disabled="!track || reviewSaving"
                maxlength="1000"
                placeholder="可选留言"
              />

              <div class="review-actions">
                <span class="review-status" :class="{ error: reviewError }">
                  {{ reviewError || (reviewLoading ? '读取中...' : reviewSavedMessage) }}
                </span>
                <button
                  v-if="trackReview"
                  type="button"
                  class="review-action ghost"
                  :disabled="reviewSaving"
                  @click="clearReview"
                >
                  清空
                </button>
                <button
                  type="button"
                  class="review-action primary"
                  :disabled="!canSaveReview"
                  @click="saveReview"
                >
                  {{ reviewSaving ? '保存中' : '保存' }}
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 底部进度 + 控制 -->
    <footer class="np-footer">
      <div class="np-progress">
        <span class="np-time">{{ player.formattedCurrentTime }}</span>
        <ProgressBar />
        <span class="np-time">{{ player.formattedDuration }}</span>
      </div>
      <div class="np-controls">
        <button class="np-ctrl" :title="modeLabel" @click="player.cyclePlayMode()">
          <AppIcon :name="modeIcon" :size="20" />
        </button>
        <button class="np-ctrl" title="上一首" :disabled="!hasQueue" @click="player.prev()">
          <AppIcon name="skip-back" :size="24" />
        </button>
        <button class="np-play" :disabled="!track" @click="player.togglePlayPause()">
          <AppIcon :name="isPlaying ? 'pause' : 'play'" :size="26" />
        </button>
        <button class="np-ctrl" title="下一首" :disabled="!hasQueue" @click="player.next()">
          <AppIcon name="skip-forward" :size="24" />
        </button>
        <button
          class="np-ctrl"
          :class="{ liked: isLiked }"
          :title="isLiked ? '取消喜欢' : '喜欢'"
          @click="toggleLike"
        >
          <AppIcon :name="isLiked ? 'heart-filled' : 'heart'" :size="20" />
        </button>
        <button
          class="np-ctrl"
          title="下载当前音频"
          :disabled="!track || isDownloading"
          @click="player.downloadCurrent()"
        >
          <AppIcon name="download" :size="20" :class="{ 'spin-slow': isDownloading }" />
        </button>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { useUiStore } from '@/stores/uiStore'
import {
  deleteTrackReview,
  fetchTrackReview,
  getTrackChapters,
  getTrackComments,
  getTrackIntro,
  getTrackSubtitles,
  mediaUrl,
  saveTrackReview,
} from '@/api/client'
import { useOpenOwner } from '@/composables/useOpenOwner'
import type { PlayMode, Track, TrackChapters, TrackComments, TrackIntro, TrackReview, TrackSubtitles } from '@/types'
import { formatCount } from '@/utils/format'
import AppIcon from '@/components/base/AppIcon.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import ProgressBar from '@/components/ProgressBar.vue'


const player = usePlayerStore()
const library = useLibraryStore()
const ui = useUiStore()
const { openTrackOwner } = useOpenOwner()

const { currentTrack, videoInfo, isPlaying, isDownloading } = storeToRefs(player)
const reducedMotion = computed(() => ui.reducedMotion)

const track = computed<Track | null>(() => {
  if (currentTrack.value) return currentTrack.value
  if (videoInfo.value) {
    return {
      trackId: videoInfo.value.trackId,
      bvid: videoInfo.value.bvid,
      cid: videoInfo.value.cid,
      title: videoInfo.value.title,
      owner: videoInfo.value.owner,
      ownerMid: videoInfo.value.ownerMid,
      cover: videoInfo.value.cover,
      duration: videoInfo.value.duration,
      playCount: videoInfo.value.playCount,
      publishedAt: videoInfo.value.publishedAt,
    }
  }
  return null
})

const coverStyle = computed(() => ({
  backgroundImage: track.value?.cover ? 'url("' + mediaUrl(track.value.cover).replace(/"/g, '%22') + '")' : 'none',
}))

const hasQueue = computed(() => player.queue.length > 0)
const isLiked = computed(() => (track.value ? library.isLiked(track.value.bvid) : false))
const ownerLine = computed(() => (track.value ? track.value.owner + ' @ Bilibili' : ''))
const statsLine = computed(() => (track.value ? track.value.bvid : ''))

const MODE_META: Record<PlayMode, { icon: string; label: string }> = {
  order: { icon: 'repeat', label: '顺序播放' },
  loop: { icon: 'repeat', label: '列表循环' },
  single: { icon: 'repeat-one', label: '单曲循环' },
  shuffle: { icon: 'shuffle', label: '随机播放' },
}
const modeIcon = computed(() => MODE_META[player.playMode].icon)
const modeLabel = computed(() => MODE_META[player.playMode].label)

type TabKey = 'subtitle' | 'intro' | 'chapters' | 'comments' | 'review'
const activeTab = ref<TabKey>('intro')
const tabs: { key: TabKey; label: string }[] = [
  { key: 'subtitle', label: '字幕' },
  { key: 'intro', label: '简介' },
  { key: 'chapters', label: '章节' },
  { key: 'comments', label: '评论区' },
  { key: 'review', label: '私人评价' },
]
const playlistMenuOpen = ref(false)

const intro = ref<TrackIntro | null>(null)
const introLoading = ref(false)
const introError = ref<string | null>(null)
const subtitles = ref<TrackSubtitles | null>(null)
const subtitleLoading = ref(false)
const subtitleError = ref<string | null>(null)
const chapters = ref<TrackChapters | null>(null)
const chaptersLoading = ref(false)
const chaptersError = ref<string | null>(null)
const comments = ref<TrackComments | null>(null)
const commentsLoading = ref(false)
const commentsLoadingMore = ref(false)
const commentsHasMore = ref(false)
const commentsMoreError = ref<string | null>(null)
const commentsError = ref<string | null>(null)
const infoPanelRef = ref<HTMLElement | null>(null)
const trackReview = ref<TrackReview | null>(null)
const reviewRating = ref(0)
const reviewMood = ref('')
const reviewCustomLabel = ref('')
const reviewNote = ref('')
const reviewLoading = ref(false)
const reviewSaving = ref(false)
const reviewError = ref<string | null>(null)
const reviewSavedMessage = ref('')

const commentsPageSize = 20
let commentsPage = 1

let detailSeq = 0

const reviewMoods = [
  '平静',
  '治愈',
  '怀旧',
  '兴奋',
  '难过',
  '专注',
  '浪漫',
  '放松',
  '温柔',
  '孤独',
  '热血',
  '清醒',
  '自由',
]
const canSaveReview = computed(() => (
  !!track.value &&
  reviewRating.value >= 1 &&
  reviewRating.value <= 5 &&
  normalizedReviewLabel.value.length >= 1 &&
  normalizedReviewLabel.value.length <= 4 &&
  !reviewLoading.value &&
  !reviewSaving.value
))
const normalizedReviewLabel = computed(() => reviewMood.value.trim())
const canApplyCustomLabel = computed(() => {
  const label = reviewCustomLabel.value.trim()
  return !!track.value && !reviewSaving.value && label.length >= 1 && label.length <= 4
})
const reviewUpdatedAt = computed(() => {
  if (!trackReview.value?.updatedAt) return ''
  return 'updated at ' + formatReviewDate(trackReview.value.updatedAt)
})

const activeSubtitleLineIndex = computed(() => {
  const lines = subtitles.value?.lines ?? []
  return findActiveSubtitleLineIndex(lines, player.currentTime)
})

watch(activeSubtitleLineIndex, (index, previousIndex) => {
  if (index < 0 || index === previousIndex || activeTab.value !== 'subtitle') return
  requestAnimationFrame(() => {
    const activeLine = infoPanelRef.value?.querySelector<HTMLElement>('[data-subtitle-index="' + index + '"]')
    scrollSubtitleLineIntoPanel(activeLine)
  })
})

function findActiveSubtitleLineIndex(lines: TrackSubtitles['lines'], currentTime: number): number {
  let low = 0
  let high = lines.length - 1
  let candidate = -1

  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (lines[middle].from <= currentTime) {
      candidate = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }

  return candidate >= 0 && currentTime < lines[candidate].to ? candidate : -1
}

watch(
  () => (track.value?.bvid ?? '') + ':' + (track.value?.cid ?? ''),
  () => {
    detailSeq++
    resetDetailPanels()
    void loadActiveTab()
    void loadTrackReview()
  },
  { immediate: true }
)

watch(activeTab, () => {
  void loadActiveTab()
  if (activeTab.value === 'comments') {
    requestAnimationFrame(() => checkCommentsScrollEdge())
  }
})

function resetDetailPanels() {
  intro.value = null
  introLoading.value = false
  introError.value = null
  subtitles.value = null
  subtitleLoading.value = false
  subtitleError.value = null
  chapters.value = null
  chaptersLoading.value = false
  chaptersError.value = null
  comments.value = null
  commentsLoading.value = false
  commentsLoadingMore.value = false
  commentsHasMore.value = false
  commentsMoreError.value = null
  commentsError.value = null
  commentsPage = 1
  resetReviewForm()
}

function resetReviewForm() {
  trackReview.value = null
  reviewRating.value = 0
  reviewMood.value = ''
  reviewCustomLabel.value = ''
  reviewNote.value = ''
  reviewLoading.value = false
  reviewSaving.value = false
  reviewError.value = null
  reviewSavedMessage.value = ''
}

async function loadTrackReview() {
  const current = track.value
  if (!current?.bvid) return
  const seq = detailSeq
  reviewLoading.value = true
  reviewError.value = null
  try {
    const review = await fetchTrackReview(current)
    if (seq !== detailSeq) return
    trackReview.value = review
    reviewRating.value = review?.rating ?? 0
    reviewMood.value = review?.mood ?? ''
    reviewCustomLabel.value = review?.mood && !reviewMoods.includes(review.mood) ? review.mood : ''
    reviewNote.value = review?.note ?? ''
  } catch (error) {
    if (seq === detailSeq) {
      reviewError.value = error instanceof Error ? error.message : '评价读取失败'
    }
  } finally {
    if (seq === detailSeq) reviewLoading.value = false
  }
}

async function loadActiveTab() {
  const current = track.value
  if (!current?.bvid) return
  const seq = detailSeq
  const bvid = current.bvid
  const cid = current.cid

  if (activeTab.value === 'intro') {
    if (intro.value || introLoading.value) return
    introLoading.value = true
    introError.value = null
    try {
      const data = await getTrackIntro(bvid, cid)
      if (seq === detailSeq) intro.value = data
    } catch (error) {
      if (seq === detailSeq) introError.value = error instanceof Error ? error.message : '简介读取失败，点击重试'
    } finally {
      if (seq === detailSeq) introLoading.value = false
    }
    return
  }

  if (activeTab.value === 'subtitle') {
    if (subtitles.value || subtitleLoading.value) return
    subtitleLoading.value = true
    subtitleError.value = null
    try {
      const data = await getTrackSubtitles(bvid, cid)
      if (cid != null && data.cid !== cid) {
        throw new Error('字幕分P与当前播放内容不一致')
      }
      if (seq === detailSeq) subtitles.value = data
    } catch (error) {
      if (seq === detailSeq) subtitleError.value = error instanceof Error ? error.message : '字幕读取失败，点击重试'
    } finally {
      if (seq === detailSeq) subtitleLoading.value = false
    }
    return
  }

  if (activeTab.value === 'chapters') {
    if (chapters.value || chaptersLoading.value) return
    chaptersLoading.value = true
    chaptersError.value = null
    try {
      const data = await getTrackChapters(bvid, cid)
      if (seq === detailSeq) chapters.value = data
    } catch (error) {
      if (seq === detailSeq) chaptersError.value = error instanceof Error ? error.message : '章节读取失败，点击重试'
    } finally {
      if (seq === detailSeq) chaptersLoading.value = false
    }
    return
  }

  if (comments.value || commentsLoading.value) return
  commentsLoading.value = true
  commentsError.value = null
  commentsMoreError.value = null
  try {
    const data = await getTrackComments(bvid, cid, 1, commentsPageSize)
    if (seq === detailSeq) {
      comments.value = data
      commentsPage = 1
      commentsHasMore.value = data.hasMore && data.comments.length > 0
      requestAnimationFrame(() => checkCommentsScrollEdge())
    }
  } catch (error) {
    if (seq === detailSeq) commentsError.value = error instanceof Error ? error.message : '评论区读取失败，点击重试'
  } finally {
    if (seq === detailSeq) commentsLoading.value = false
  }
}

async function loadMoreComments(force = false) {
  const current = track.value
  if (
    activeTab.value !== 'comments' ||
    !current?.bvid ||
    !comments.value ||
    commentsLoading.value ||
    commentsLoadingMore.value ||
    !commentsHasMore.value ||
    (!force && commentsMoreError.value)
  ) {
    return
  }

  const seq = detailSeq
  const bvid = current.bvid
  const cid = current.cid
  const nextPage = commentsPage + 1
  const currentComments = comments.value.comments
  commentsLoadingMore.value = true
  commentsMoreError.value = null

  try {
    const data = await getTrackComments(bvid, cid, nextPage, commentsPageSize)
    if (seq !== detailSeq) return

    comments.value = {
      ...data,
      page: nextPage,
      pageSize: commentsPageSize,
      comments: mergeComments(currentComments, data.comments),
    }
    commentsPage = nextPage
    commentsHasMore.value = data.hasMore && data.comments.length > 0
  } catch (error) {
    if (seq === detailSeq) {
      commentsMoreError.value = error instanceof Error ? error.message : '加载更多评论失败，点击重试'
    }
  } finally {
    if (seq === detailSeq) {
      commentsLoadingMore.value = false
      requestAnimationFrame(() => checkCommentsScrollEdge())
    }
  }
}

function mergeComments(
  current: TrackComments['comments'],
  incoming: TrackComments['comments']
): TrackComments['comments'] {
  const seen = new Set(current.map((comment) => comment.id))
  const merged = [...current]
  for (const comment of incoming) {
    if (seen.has(comment.id)) continue
    seen.add(comment.id)
    merged.push(comment)
  }
  return merged
}

function handleInfoPanelScroll(event: Event) {
  if (activeTab.value !== 'comments') return
  checkCommentsScrollEdge(event.currentTarget as HTMLElement)
}

function checkCommentsScrollEdge(panel = infoPanelRef.value) {
  if (!panel || activeTab.value !== 'comments') return
  if (isNearBottom(panel)) {
    void loadMoreComments()
  }
}

function isNearBottom(panel: HTMLElement, threshold = 72): boolean {
  return panel.scrollTop + panel.clientHeight >= panel.scrollHeight - threshold
}

function scrollSubtitleLineIntoPanel(activeLine?: HTMLElement | null) {
  const panel = infoPanelRef.value
  if (!panel || !activeLine) return

  const panelRect = panel.getBoundingClientRect()
  const lineRect = activeLine.getBoundingClientRect()
  const targetTop = panel.scrollTop + (lineRect.top - panelRect.top) - (panel.clientHeight - lineRect.height) / 2
  const maxTop = Math.max(0, panel.scrollHeight - panel.clientHeight)
  const nextTop = Math.min(Math.max(0, targetTop), maxTop)
  panel.scrollTo({
    top: nextTop,
    behavior: reducedMotion.value ? 'auto' : 'smooth',
  })
}

function formatTimeLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return mins.toString().padStart(2, '0') + ':' + secs.toString().padStart(2, '0')
}

function formatReviewDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return String(date.getMonth() + 1) + '/' + String(date.getDate()) + ' ' + date.getHours().toString().padStart(2, '0') + ':' + date.getMinutes().toString().padStart(2, '0')
}

async function saveReview() {
  if (!track.value || !canSaveReview.value) return
  const current = track.value
  const seq = detailSeq
  reviewSaving.value = true
  reviewError.value = null
  reviewSavedMessage.value = ''
  try {
    const review = await saveTrackReview(current, reviewRating.value, normalizedReviewLabel.value, reviewNote.value)
    if (seq !== detailSeq) return
    trackReview.value = review
    reviewRating.value = review.rating
    reviewMood.value = review.mood
    reviewCustomLabel.value = review.mood && !reviewMoods.includes(review.mood) ? review.mood : ''
    reviewNote.value = review.note
    reviewSavedMessage.value = '已保存'
  } catch (error) {
    if (seq === detailSeq) {
      reviewError.value = error instanceof Error ? error.message : '评价保存失败'
    }
  } finally {
    if (seq === detailSeq) reviewSaving.value = false
  }
}

async function clearReview() {
  if (!track.value) return
  const current = track.value
  const seq = detailSeq
  reviewSaving.value = true
  reviewError.value = null
  try {
    await deleteTrackReview(current)
    if (seq !== detailSeq) return
    trackReview.value = null
    reviewRating.value = 0
    reviewMood.value = ''
    reviewCustomLabel.value = ''
    reviewNote.value = ''
    reviewSavedMessage.value = '已清空'
  } catch (error) {
    if (seq === detailSeq) {
      reviewError.value = error instanceof Error ? error.message : '评价清空失败'
    }
  } finally {
    if (seq === detailSeq) reviewSaving.value = false
  }
}

function toggleLike() {
  if (track.value) library.toggleLike(track.value)
}

function selectReviewLabel(label: string) {
  reviewMood.value = label
  reviewCustomLabel.value = ''
}

function syncCustomLabel() {
  const label = reviewCustomLabel.value.trim()
  if (label.length >= 1 && label.length <= 4) {
    reviewMood.value = label
  }
}

function applyCustomLabel() {
  if (!canApplyCustomLabel.value) return
  reviewMood.value = reviewCustomLabel.value.trim()
}

function openOwner() {
  void openTrackOwner(track.value).then((opened) => {
    if (opened) ui.closeNowPlaying()
    else player.statusMessage = '无法打开 UP 主页：缺少 UP 主 ID'
  }).catch((error) => {
    player.statusMessage = error instanceof Error ? error.message : '无法打开 UP 主页'
  })
}

function enqueueCurrent() {
  if (track.value) player.enqueue(track.value)
}

function addCurrentToPlaylist(playlistId: string) {
  if (!track.value) return
  library.addToPlaylist(playlistId, track.value)
  playlistMenuOpen.value = false
}
</script>

<style scoped>
.now-playing {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #0e0e12;
  color: #fff;
}

/* ===== 品牌氛围背景：低频缓动模糊封面 ===== */
.ambient {
  position: absolute;
  inset: 0;
  z-index: 0;
}

.ambient-layer {
  position: absolute;
  inset: -80px;
  background-position: center;
  background-size: cover;
  filter: blur(60px);
  transform: scale(1.15);
  will-change: transform;
}

.layer-1 {
  opacity: 0.9;
  animation: drift1 40s ease-in-out infinite;
}
.layer-2 {
  opacity: 0.5;
  mix-blend-mode: screen;
  animation: drift2 55s ease-in-out infinite;
}
.layer-3 {
  opacity: 0.35;
  mix-blend-mode: overlay;
  animation: drift3 70s ease-in-out infinite;
}

/* 动画非常慢、幅度很小，避免抢过歌曲信息。 */
@keyframes drift1 {
  0%, 100% { transform: scale(1.15) translate(0, 0); }
  50% { transform: scale(1.2) translate(2%, -1.5%); }
}
@keyframes drift2 {
  0%, 100% { transform: scale(1.18) translate(0, 0) rotate(0deg); }
  50% { transform: scale(1.22) translate(-2%, 1.5%) rotate(1.5deg); }
}
@keyframes drift3 {
  0%, 100% { transform: scale(1.2) translate(0, 0); }
  50% { transform: scale(1.15) translate(1.5%, 2%); }
}

/* 降级：低性能、reduced-motion、暂停时保持静止。 */
.ambient.still .ambient-layer {
  animation: none;
}

.ambient-mask {
  position: absolute;
  inset: 0;
  z-index: 1;
  background: linear-gradient(
    180deg,
    rgba(10, 10, 14, 0.58) 0%,
    rgba(10, 10, 14, 0.74) 58%,
    rgba(10, 10, 14, 0.88) 100%
  );
}

/* ===== 顶栏 ===== */
.np-header {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 20px;
}

.np-icon-btn {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease;
}
.np-icon-btn:hover {
  background: rgba(255, 255, 255, 0.16);
}

.np-brand {
  flex: 1;
  text-align: center;
  font-size: 14px;
  letter-spacing: 1px;
  color: rgba(255, 255, 255, 0.8);
}
.np-spacer {
  width: 38px;
}

/* ===== 主体 ===== */
.np-body {
  position: relative;
  z-index: 2;
  flex: 1;
  display: flex;
  align-items: stretch;
  justify-content: center;
  gap: 64px;
  padding: 8px 8% 14px;
  min-height: 0;
}

/* 黑胶 */
.disc-side {
  position: relative;
  align-self: center;
  flex-shrink: 0;
  width: 380px;
  height: 380px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.vinyl {
  width: 340px;
  height: 340px;
}

.vinyl-disc {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: radial-gradient(circle at center, #2a2a2f 0%, #141417 60%, #0c0c0f 100%);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
}

.vinyl.spinning {
  animation: spin 18s linear infinite;
}
.vinyl.paused {
  animation: spin 18s linear infinite;
  animation-play-state: paused;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.vinyl-grooves {
  position: absolute;
  inset: 20px;
  border-radius: 50%;
  background: repeating-radial-gradient(
    circle at center,
    rgba(255, 255, 255, 0.03) 0px,
    rgba(255, 255, 255, 0.03) 1px,
    transparent 2px,
    transparent 5px
  );
}

.vinyl-label {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 44%;
  height: 44%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  overflow: hidden;
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.06);
}

.vinyl-label img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.label-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #26262b;
  color: rgba(255, 255, 255, 0.5);
}

.vinyl-hole {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 12px;
  height: 12px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: #0e0e12;
  box-shadow: inset 0 0 3px rgba(255, 255, 255, 0.3);
}

/* 鍞遍拡 */
.tonearm {
  position: absolute;
  top: -10px;
  right: 40px;
  z-index: 3;
  width: 60px;
  height: 120px;
  transform-origin: 12px 12px;
  transform: rotate(-22deg);
  transition: transform 500ms ease;
}
.tonearm.playing {
  transform: rotate(0deg);
}

/* 淇℃伅鍖?*/
.info-side {
  flex: 1;
  min-width: 0;
  min-height: 0;
  align-self: center;
  max-height: min(660px, 100%);
  display: grid;
  grid-template-rows: auto auto minmax(230px, 1fr);
  gap: 14px;
  max-width: 560px;
}

.track-head {
  min-width: 0;
}

.np-title {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.np-owner {
  margin-top: 10px;
  font-size: 15px;
  color: rgba(255, 255, 255, 0.75);
}

.np-owner-link {
  display: block;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  transition: color 160ms ease;
}

.np-owner-link:hover {
  color: #fff;
}

.np-stats {
  margin-top: 6px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  font-variant-numeric: tabular-nums;
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  flex-wrap: wrap;
}

.detail-btn {
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.86);
  font-size: 13px;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.detail-btn:hover:not(:disabled) {
  background: rgba(251, 114, 153, 0.18);
  border-color: rgba(251, 114, 153, 0.42);
  color: #fff;
}

.detail-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.playlist-menu {
  margin-top: 10px;
  width: min(360px, 100%);
  display: grid;
  gap: 6px;
  padding: 8px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: var(--radius-medium);
  background: rgba(12, 12, 16, 0.6);
}

.playlist-option {
  min-width: 0;
  height: 32px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 9px;
  border: none;
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.86);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.playlist-option:hover:not(:disabled) {
  background: rgba(251, 114, 153, 0.2);
  color: #fff;
}

.playlist-option:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.playlist-option span {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: left;
}

.playlist-option small,
.playlist-empty {
  flex-shrink: 0;
  color: rgba(255, 255, 255, 0.46);
  font-size: 12px;
}

.playlist-empty {
  padding: 4px 2px;
}

.info-tabs {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  padding-bottom: 4px;
}

.tab-btn {
  padding: 6px 14px;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.6);
  font-size: 14px;
  cursor: pointer;
  border-radius: var(--radius-small);
  transition: color 160ms ease, background 160ms ease;
}
.tab-btn:hover {
  color: #fff;
}
.tab-btn.active {
  color: var(--color-primary);
}

.info-panel {
  overflow-y: auto;
  min-height: 0;
  max-height: none;
  padding-right: 6px;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.info-panel::-webkit-scrollbar {
  width: 8px;
}

.info-panel::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 999px;
}

.info-panel::-webkit-scrollbar-thumb {
  background: rgba(251, 114, 153, 0.42);
  border-radius: 999px;
}

.info-panel::-webkit-scrollbar-thumb:hover {
  background: rgba(251, 114, 153, 0.62);
}

.panel-text {
  font-size: 14px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.82);
}
.panel-text.muted {
  color: rgba(255, 255, 255, 0.5);
}

.panel-error {
  width: 100%;
  min-height: 38px;
  border: 1px solid rgba(251, 114, 153, 0.34);
  border-radius: var(--radius-small);
  background: rgba(251, 114, 153, 0.12);
  color: rgba(255, 255, 255, 0.9);
  cursor: pointer;
}

.intro-panel,
.subtitle-panel,
.chapter-list,
.comment-list {
  display: grid;
  gap: 10px;
}

.intro-stats,
.subtitle-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.intro-stats span,
.subtitle-meta span {
  height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.68);
  font-size: 12px;
}

.subtitle-meta span.active {
  background: rgba(251, 114, 153, 0.2);
  color: #fff;
}

.intro-text,
.dynamic-text {
  white-space: pre-wrap;
}

.dynamic-text {
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.62);
}

.subtitle-lines {
  list-style: none;
  display: grid;
  gap: 8px;
}

.subtitle-line-button,
.chapter-item {
  display: grid;
  grid-template-columns: 54px 1fr;
  align-items: start;
  gap: 10px;
}

.subtitle-lines li {
  border-left: 2px solid transparent;
  border-radius: var(--radius-small);
  scroll-margin-block: 72px;
  transition: background 160ms ease, border-color 160ms ease;
}

.subtitle-lines li.active {
  border-left-color: var(--color-primary);
  background: rgba(251, 114, 153, 0.14);
}

.subtitle-line-button {
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.subtitle-line-button:hover {
  background: rgba(255, 255, 255, 0.06);
}

.time-badge {
  width: 48px;
  padding-top: 1px;
  font-size: 12px;
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
}

.subtitle-lines span:last-child {
  color: rgba(255, 255, 255, 0.82);
  font-size: 14px;
  line-height: 1.65;
}

.chapter-item {
  width: 100%;
  min-height: 42px;
  padding: 9px 10px;
  border: none;
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.07);
  color: rgba(255, 255, 255, 0.84);
  cursor: pointer;
  text-align: left;
  transition: background 160ms ease, color 160ms ease;
}

.chapter-item:hover {
  background: rgba(251, 114, 153, 0.16);
  color: #fff;
}

.comment-item {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 10px;
  padding: 10px;
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.07);
}

.comment-item img,
.comment-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}

.comment-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(251, 114, 153, 0.2);
  color: #fff;
  font-size: 13px;
}

.comment-body {
  min-width: 0;
}

.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  color: rgba(255, 255, 255, 0.9);
  font-size: 13px;
}

.comment-head span {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.comment-head small {
  flex-shrink: 0;
  color: rgba(255, 255, 255, 0.48);
}

.comment-body p {
  font-size: 13px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.76);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.panel-more-loading,
.panel-more-btn,
.panel-end {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.56);
  font-size: 13px;
}

.panel-more-btn {
  width: 100%;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.07);
  color: rgba(255, 255, 255, 0.78);
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.panel-more-btn:hover {
  background: rgba(251, 114, 153, 0.16);
  border-color: rgba(251, 114, 153, 0.38);
  color: #fff;
}

.panel-more-btn.error {
  border-color: rgba(251, 114, 153, 0.36);
  background: rgba(251, 114, 153, 0.12);
}

.review-panel {
  min-height: 0;
  display: grid;
  gap: 12px;
  padding: 2px 0 8px;
}

.review-head,
.review-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.review-head h2 {
  margin: 0;
  font-size: 14px;
  line-height: 1.2;
  color: rgba(255, 255, 255, 0.92);
}

.review-head p {
  margin-top: 3px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}

.review-private {
  height: 22px;
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.58);
  font-size: 11px;
  letter-spacing: 0;
}

.review-stars,
.review-moods {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.review-star {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.07);
  color: rgba(255, 255, 255, 0.46);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease, transform 120ms ease;
}

.review-star:hover:not(:disabled),
.review-star.active {
  background: rgba(251, 114, 153, 0.18);
  color: #ffcf5a;
}

.review-star:active:not(:disabled) {
  transform: scale(0.94);
}

.review-mood {
  height: 28px;
  padding: 0 10px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.68);
  font-size: 12px;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.review-mood:hover:not(:disabled),
.review-mood.active {
  border-color: rgba(251, 114, 153, 0.42);
  background: rgba(251, 114, 153, 0.17);
  color: #fff;
}

.review-custom-label {
  display: flex;
  align-items: center;
  gap: 8px;
}

.review-custom-label input {
  width: 120px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radius-small);
  background: rgba(0, 0, 0, 0.24);
  color: rgba(255, 255, 255, 0.86);
  font-size: 12px;
  outline: none;
}

.review-custom-label input:focus {
  border-color: rgba(251, 114, 153, 0.5);
}

.review-custom-label input::placeholder {
  color: rgba(255, 255, 255, 0.38);
}

.review-note {
  width: 100%;
  min-height: 54px;
  max-height: 96px;
  resize: vertical;
  padding: 9px 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.24);
  color: rgba(255, 255, 255, 0.86);
  font-size: 13px;
  line-height: 1.5;
  outline: none;
}

.review-note:focus {
  border-color: rgba(251, 114, 153, 0.5);
}

.review-note::placeholder {
  color: rgba(255, 255, 255, 0.38);
}

.review-status {
  min-width: 0;
  flex: 1;
  color: rgba(255, 255, 255, 0.48);
  font-size: 12px;
}

.review-status.error {
  color: var(--color-primary);
}

.review-action {
  height: 30px;
  padding: 0 12px;
  border-radius: var(--radius-small);
  font-size: 12px;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.review-action.ghost {
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: rgba(255, 255, 255, 0.68);
}

.review-action.primary {
  border: 1px solid rgba(251, 114, 153, 0.55);
  background: var(--color-primary);
  color: #fff;
}

.review-action:hover:not(:disabled) {
  border-color: rgba(251, 114, 153, 0.56);
  background: rgba(251, 114, 153, 0.2);
  color: #fff;
}

.review-action:disabled,
.review-star:disabled,
.review-mood:disabled,
.review-custom-label input:disabled,
.review-note:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== 搴曢儴 ===== */
.np-footer {
  position: relative;
  z-index: 2;
  flex-shrink: 0;
  padding: 12px 8% 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.np-progress {
  display: flex;
  align-items: center;
  gap: 12px;
}

.np-time {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
  font-variant-numeric: tabular-nums;
  min-width: 42px;
  text-align: center;
}

.np-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
}

.np-ctrl {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.85);
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}
.np-ctrl:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}
.np-ctrl:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.np-ctrl.liked {
  color: var(--color-primary);
}

.np-play {
  width: 60px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: var(--color-primary);
  color: #fff;
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease, transform 120ms ease;
  box-shadow: 0 4px 16px rgba(251, 114, 153, 0.5);
}
.np-play:hover:not(:disabled) {
  background: var(--color-primary-hover);
}
.np-play:active:not(:disabled) {
  transform: scale(0.94);
}
.np-play:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 响应式：窄屏堆叠 */
@media (max-width: 900px) {
  .np-body {
    flex-direction: column;
    justify-content: center;
    gap: 32px;
    padding: 0 24px 16px;
    overflow-y: auto;
  }
  .disc-side {
    width: 260px;
    height: 260px;
  }
  .vinyl {
    width: 240px;
    height: 240px;
  }
  .info-side {
    width: 100%;
    align-self: stretch;
    height: auto;
    display: flex;
    flex-direction: column;
    max-height: none;
  }
  .info-panel {
    flex: none;
    max-height: min(300px, 42vh);
  }
}

.np-ctrl .spin-slow {
  animation: spin 1.2s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .vinyl.spinning { animation: none; }
  .ambient-layer { animation: none !important; }
  .tonearm { transition: none; }
  .np-ctrl .spin-slow { animation: none; }
}
</style>
