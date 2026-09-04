<template>
  <div class="track-row-wrap">
    <div
      class="track-row"
      :class="{ active: isCurrent }"
      @dblclick="handlePrimaryPlay"
    >
      <div class="col-index">
        <PlayingBars v-if="isCurrent && isPlaying" />
        <span v-else-if="!isPreparingPlay" class="index-num">{{ index + 1 }}</span>
        <button
          class="row-play"
          :class="{ preparing: isPreparingPlay }"
          :title="isPreparingPlay ? '正在准备播放' : '播放'"
          :disabled="isPreparingPlay"
          @click.stop="handlePrimaryPlay"
        >
          <LoadingDots v-if="isPreparingPlay" />
          <AppIcon v-else name="play" :size="16" />
        </button>
      </div>

      <img class="col-cover" :src="mediaUrl(track.cover)" :alt="track.title" loading="lazy" />

      <div class="col-main">
        <div class="row-title" :class="{ 'is-current': isCurrent }" :title="track.title">
          {{ track.title }}
        </div>
        <button
          v-if="track.bvid && !isPreparingPlay"
          class="row-owner owner-link"
          type="button"
          :title="`打开 UP 主页：${track.owner}`"
          @click.stop="openOwner"
        >
          {{ track.owner }}
        </button>
        <div v-else class="row-owner" :class="{ preparing: isPreparingPlay }">
          {{ isPreparingPlay ? '正在准备播放' : track.owner }}
        </div>
      </div>

      <div class="col-duration">{{ formatDuration(track.duration) }}</div>

      <div class="col-actions">
        <button
          class="action-btn"
          :class="{ liked: isLiked }"
          :title="isLiked ? '取消喜欢' : '喜欢'"
          @click.stop="$emit('like')"
        >
          <AppIcon :name="isLiked ? 'heart-filled' : 'heart'" :size="16" />
        </button>
        <button class="action-btn" :title="enqueueTitle" @click.stop="handleEnqueue($event)">
          <AppIcon name="plus" :size="16" />
        </button>
        <button class="action-btn" title="添加到歌单" @click.stop="handlePlaylist">
          <AppIcon name="list" :size="16" />
        </button>
        <button v-if="removable" class="action-btn" title="删除" @click.stop="$emit('remove')">
          <AppIcon name="close" :size="16" />
        </button>
      </div>
    </div>

    <div v-if="playlistTarget" class="playlist-menu" @click.stop>
      <div class="playlist-menu-head">
        <span>添加到歌单</span>
        <button class="playlist-close" title="关闭" @click="playlistTarget = null">
          <AppIcon name="close" :size="14" />
        </button>
      </div>
      <div class="playlist-target" :title="playlistTarget.title">{{ playlistTarget.title }}</div>
      <div v-if="library.playlists.length" class="playlist-options">
        <button
          v-for="playlist in library.playlists"
          :key="playlist.id"
          class="playlist-option"
          :disabled="library.hasPlaylistTrack(playlist.id, playlistTarget)"
          @click="addTargetToPlaylist(playlist.id)"
        >
          <AppIcon name="list" :size="14" />
          <span>{{ playlist.name }}</span>
          <small v-if="library.hasPlaylistTrack(playlist.id, playlistTarget)">已存在</small>
        </button>
      </div>
      <div v-else class="playlist-empty">先在侧边栏新建歌单</div>
    </div>

    <div v-if="showPartsPanel" class="parts-panel" @click.stop>
      <div v-if="partsLoading" class="parts-state">正在读取分 P</div>
      <button v-else-if="partsError" class="parts-state error" @click="retryOpenParts">
        {{ partsError }}
      </button>
      <div v-else class="parts-list">
        <div v-for="part in parts" :key="part.trackId ?? `${part.bvid}:${part.cid}`" class="part-row">
          <button class="part-title-btn" :title="part.title" @click="playPart(part)">
            <span class="part-index">P{{ part.page ?? '?' }}</span>
            <span>{{ partDisplayTitle(part) }}</span>
          </button>
          <div class="part-actions">
            <button
              class="part-btn"
              :class="{ liked: library.isTrackLiked(part) }"
              :title="library.isTrackLiked(part) ? '取消喜欢' : '喜欢'"
              @click="toggleLikePart(part)"
            >
              <AppIcon :name="library.isTrackLiked(part) ? 'heart-filled' : 'heart'" :size="14" />
            </button>
            <button class="part-btn" title="加入队列" @click="enqueuePart(part, $event)">
              <AppIcon name="plus" :size="14" />
            </button>
            <button class="part-btn" title="添加到歌单" @click="openPlaylistMenu(part)">
              <AppIcon name="list" :size="14" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Track } from '@/types'
import { getTrackDetail, mediaUrl } from '@/api/client'
import { useOpenOwner } from '@/composables/useOpenOwner'
import { useLibraryStore } from '@/stores/libraryStore'
import { usePlayerStore } from '@/stores/playerStore'
import { formatDuration } from '@/utils/format'
import AppIcon from '@/components/base/AppIcon.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import PlayingBars from '@/components/base/PlayingBars.vue'

const DETAIL_TIMEOUT_MS = 10000
const partCache = new Map<string, Track[]>()

type TrackWithLegacyOwner = Track & {
  owner_mid?: unknown
  mid?: unknown
  upper?: { mid?: unknown } | null
  ownerInfo?: { mid?: unknown } | null
}

const props = defineProps<{
  track: Track
  index: number
  isCurrent?: boolean
  isPlaying?: boolean
  isLiked?: boolean
  removable?: boolean
}>()

const emit = defineEmits<{
  play: []
  like: []
  enqueue: []
  remove: []
}>()

const player = usePlayerStore()
const library = useLibraryStore()
const { openTrackOwner } = useOpenOwner()

const parts = ref<Track[]>([])
const partsOpen = ref(false)
const partsLoading = ref(false)
const partsLoaded = ref(false)
const partsError = ref<string | null>(null)
const isPreparingPlay = ref(false)
const loadedBvid = ref<string | null>(null)
const playlistTarget = ref<Track | null>(null)

let loadPromise: Promise<Track[]> | null = null

const canLoadParts = computed(() => !!props.track.bvid)
const knownPageCount = computed<number | null>(() => {
  if (partsLoaded.value && loadedBvid.value === props.track.bvid) {
    return parts.value.length
  }
  if (typeof props.track.pageCount === 'number' && Number.isFinite(props.track.pageCount) && props.track.pageCount > 0) {
    return Math.trunc(props.track.pageCount)
  }
  if (props.track.isMultipart === true) return 2
  if (props.track.isMultipart === false) return 1
  return null
})
const isKnownMultipart = computed(() => (knownPageCount.value ?? 0) > 1)
const isKnownSinglePart = computed(() => knownPageCount.value === 1)
const showPartsPanel = computed(() => {
  return canLoadParts.value
    && partsOpen.value
    && (partsLoading.value || !!partsError.value || parts.value.length > 1)
})
const enqueueTitle = computed(() => isKnownMultipart.value ? '添加全部分 P 到队列' : '添加到队列')

function resetPartsIfTrackChanged() {
  if (loadedBvid.value === props.track.bvid) return
  loadedBvid.value = props.track.bvid
  parts.value = []
  partsOpen.value = false
  partsLoaded.value = false
  partsLoading.value = false
  partsError.value = null
  loadPromise = null
}

async function ensureParts(): Promise<Track[]> {
  if (!canLoadParts.value) return []
  resetPartsIfTrackChanged()
  if (partsLoaded.value) return parts.value
  if (loadPromise) return loadPromise

  const bvid = props.track.bvid
  const cached = partCache.get(bvid)
  if (cached) {
    parts.value = normalizeParts(cached)
    partsLoaded.value = true
    partsError.value = null
    return parts.value
  }

  partsLoading.value = true
  partsError.value = null

  loadPromise = loadTrackParts(bvid)
    .then((detailParts) => {
      parts.value = normalizeParts(detailParts)
      partsLoaded.value = true
      partsError.value = null
      return parts.value
    })
    .catch((error) => {
      partsLoaded.value = false
      partsError.value = error instanceof Error ? error.message : '分 P 读取失败，点击重试'
      return []
    })
    .finally(() => {
      partsLoading.value = false
      loadPromise = null
    })

  return loadPromise
}

async function handlePrimaryPlay() {
  if (isPreparingPlay.value) return
  resetPartsIfTrackChanged()
  if (props.track.cid != null || isKnownSinglePart.value) {
    emit('play')
    return
  }

  if (isKnownMultipart.value) {
    if (partsLoaded.value && parts.value.length > 1) {
      partsOpen.value = !partsOpen.value
      return
    }

    partsOpen.value = true
    const pageTracks = await ensureParts()
    if (pageTracks.length === 1) {
      partsOpen.value = false
      player.playTrack(pageTracks[0])
    }
    return
  }

  // Unknown page counts require one detail probe. Keep it invisible until multipart is confirmed.
  isPreparingPlay.value = true
  const pageTracks = await ensureParts().finally(() => {
    isPreparingPlay.value = false
  })
  if (pageTracks.length > 1) {
    partsOpen.value = true
    return
  }
  if (pageTracks[0]) {
    partsOpen.value = false
    player.playTrack(pageTracks[0])
    return
  }
  partsOpen.value = false
  emit('play')
}

async function handleEnqueue(event: MouseEvent) {
  resetPartsIfTrackChanged()
  if (props.track.cid != null || isKnownSinglePart.value) {
    emitQueueAddEffect(event)
    emit('enqueue')
    return
  }

  const pageTracks = await ensureParts()
  if (pageTracks.length > 1) {
    partsOpen.value = true
    enqueueParts(pageTracks, event)
    return
  }
  if (pageTracks[0]) {
    if (player.enqueue(pageTracks[0])) {
      emitQueueAddEffect(event)
    }
    return
  }
  emitQueueAddEffect(event)
  emit('enqueue')
}

async function handlePlaylist() {
  resetPartsIfTrackChanged()
  if (props.track.cid != null || isKnownSinglePart.value) {
    openPlaylistMenu(props.track)
    return
  }

  const pageTracks = await ensureParts()
  if (pageTracks.length > 1) {
    partsOpen.value = true
    return
  }
  if (pageTracks[0]) {
    openPlaylistMenu(pageTracks[0])
    return
  }
  openPlaylistMenu(props.track)
}

async function retryOpenParts() {
  partsLoaded.value = false
  partsError.value = null
  partsOpen.value = true
  await ensureParts()
}

function partDisplayTitle(part: Track): string {
  return part.pageTitle || part.title
}

function playPart(part: Track) {
  partsOpen.value = false
  player.playTrack(part)
}

function enqueuePart(part: Track, event: MouseEvent) {
  if (player.enqueue(part)) {
    emitQueueAddEffect(event)
  }
}

function enqueueParts(pageTracks: Track[], event: MouseEvent) {
  if (player.enqueueTracks(pageTracks) > 0) {
    emitQueueAddEffect(event)
  }
}

function emitQueueAddEffect(event: MouseEvent) {
  const target = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  const rect = target?.getBoundingClientRect()
  window.dispatchEvent(new CustomEvent('bili-radio:queue-add-effect', {
    detail: {
      x: rect ? rect.left + rect.width / 2 : event.clientX,
      y: rect ? rect.top + rect.height / 2 : event.clientY,
    },
  }))
}

function openOwner() {
  void openTrackOwner(props.track).then((opened) => {
    if (!opened) {
      player.statusMessage = '无法打开 UP 主页：缺少 UP 主 ID'
    }
  }).catch((error) => {
    player.statusMessage = error instanceof Error ? error.message : '无法打开 UP 主页'
  })
}

function toggleLikePart(part: Track) {
  library.toggleLike(part)
}

function openPlaylistMenu(track: Track) {
  playlistTarget.value = track
}

function addTargetToPlaylist(playlistId: string) {
  if (!playlistTarget.value) return
  library.addToPlaylist(playlistId, playlistTarget.value)
  playlistTarget.value = null
}

function normalizeParts(detailParts: Track[]): Track[] {
  const pageCount = detailParts.length
  return detailParts
    .filter((part) => part.cid != null)
    .map((part) => ({
      ...props.track,
      ...part,
      cover: part.cover || props.track.cover,
      owner: part.owner || props.track.owner,
      ownerMid: ownerMidFromTrack(part) ?? ownerMidFromTrack(props.track),
      playCount: part.playCount ?? props.track.playCount,
      publishedAt: part.publishedAt ?? props.track.publishedAt,
      source: part.source ?? props.track.source,
      pageCount,
      isMultipart: pageCount > 1,
    }))
}

function ownerMidFromTrack(track: Track): number | null {
  const raw = track as TrackWithLegacyOwner
  return normalizedMid(raw.ownerMid)
    ?? normalizedMid(raw.owner_mid)
    ?? normalizedMid(raw.mid)
    ?? normalizedMid(raw.upper?.mid)
    ?? normalizedMid(raw.ownerInfo?.mid)
}

function normalizedMid(value: unknown): number | null {
  const mid = Number(value)
  return Number.isFinite(mid) && mid > 0 ? mid : null
}

function loadTrackParts(bvid: string): Promise<Track[]> {
  return withTimeout(getTrackDetail(bvid), DETAIL_TIMEOUT_MS)
    .then((detail) => {
      const detailParts = detail.pages.length ? detail.pages : [detail.track]
      partCache.set(bvid, detailParts)
      return detailParts
    })
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('分 P 读取超时，点击重试'))
    }, timeoutMs)

    promise
      .then(resolve)
      .catch(reject)
      .finally(() => clearTimeout(timer))
  })
}
</script>

<style scoped>
.track-row-wrap {
  min-width: 0;
}

.track-row {
  display: grid;
  grid-template-columns: 40px 44px 1fr auto auto;
  align-items: center;
  gap: 16px;
  height: 64px;
  padding: 0 12px;
  border-radius: var(--radius-small);
  cursor: default;
  transition: background 160ms ease;
  user-select: none;
}

.track-row:hover {
  background: var(--color-bg-hover);
}

.track-row.active {
  background: var(--color-primary-soft);
}

.col-index {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--color-text-tertiary);
}

.index-num {
  font-variant-numeric: tabular-nums;
}

.row-play {
  position: absolute;
  inset: 0;
  display: none;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
}

.row-play.preparing {
  display: flex;
  cursor: progress;
}

.track-row:hover .index-num {
  opacity: 0;
}

.track-row:hover .row-play {
  display: flex;
}

.col-cover {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-small);
  object-fit: cover;
  background: var(--color-bg-hover);
}

.col-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.row-title {
  font-size: 14px;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-title.is-current {
  color: var(--color-primary);
}

.row-owner {
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.owner-link {
  min-width: 0;
  border: none;
  background: transparent;
  padding: 0;
  text-align: left;
  cursor: pointer;
  transition: color 160ms ease;
}

.owner-link:hover {
  color: var(--color-primary);
}

.row-owner.preparing {
  color: var(--color-primary);
}

.col-duration {
  font-size: 13px;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
  min-width: 44px;
  text-align: right;
}

.col-actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 160ms ease;
}

.track-row:hover .col-actions {
  opacity: 1;
}

.action-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 50%;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.action-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--color-text-primary);
}

[data-theme='dark'] .action-btn:hover {
  background: rgba(255, 255, 255, 0.1);
}

.action-btn.liked {
  color: var(--color-primary);
  opacity: 1;
}

.track-row .col-actions:has(.liked) {
  opacity: 1;
}

.playlist-menu {
  margin: 4px 12px 8px 96px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-medium);
  background: var(--color-bg-content);
  box-shadow: var(--shadow-popup);
}

.playlist-menu-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.playlist-close {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.playlist-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.playlist-target {
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.playlist-options {
  margin-top: 8px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 6px;
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
  background: var(--color-bg-app);
  color: var(--color-text-primary);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.playlist-option:hover:not(:disabled) {
  background: var(--color-primary-soft);
  color: var(--color-primary);
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

.playlist-option small {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

.playlist-empty {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.parts-panel {
  margin: 4px 12px 8px 96px;
  max-height: 280px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(251, 114, 153, 0.28);
  border-radius: var(--radius-medium);
  background: color-mix(in srgb, var(--color-bg-content) 96%, var(--color-primary) 4%);
}

.parts-state {
  min-height: 54px;
  display: grid;
  place-items: center;
  padding: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.parts-state.error {
  width: 100%;
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
}

.parts-list {
  overflow-y: auto;
  padding: 6px;
}

.part-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 4px 6px;
  border-radius: var(--radius-small);
}

.part-row:hover {
  background: var(--color-bg-hover);
}

.part-title-btn {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: transparent;
  color: var(--color-text-primary);
  text-align: left;
  cursor: pointer;
}

.part-title-btn span:last-child {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
}

.part-title-btn:hover span:last-child {
  color: var(--color-primary);
}

.part-index {
  flex-shrink: 0;
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 700;
}

.part-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.part-btn {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.part-btn:hover {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.part-btn.liked {
  color: var(--color-primary);
}

@media (max-width: 720px) {
  .playlist-menu,
  .parts-panel {
    margin: 4px 8px 8px 8px;
  }
}
</style>
