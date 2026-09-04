<template>
  <div class="page up-page">
    <header class="up-head">
      <img v-if="profile?.face" class="up-avatar" :src="mediaUrl(profile.face)" :alt="profile.name" />
      <div v-else class="up-avatar fallback">
        <AppIcon name="user" :size="34" />
      </div>
      <div class="up-info">
        <span class="kind">UP 主</span>
        <h1>{{ profile?.name || '正在读取' }}</h1>
        <p class="up-sign">{{ profile?.sign || '暂无简介' }}</p>
      </div>
    </header>

    <section class="section">
      <div class="section-head">
        <div>
          <h2>稿件</h2>
          <p>{{ total > 0 ? `${total} 个视频` : '仅展示公开稿件' }}</p>
        </div>
        <div class="order-tabs" role="tablist" aria-label="稿件排序">
          <button
            v-for="item in orderOptions"
            :key="item.value"
            class="order-tab"
            :class="{ active: order === item.value }"
            type="button"
            @click="setOrder(item.value)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <label v-if="tracks.length" class="local-search">
        <AppIcon name="search" :size="16" />
        <input v-model="query" type="search" placeholder="搜索这个 UP 的稿件" />
        <button v-if="query" type="button" title="清空搜索" @click="query = ''">
          <AppIcon name="close" :size="14" />
        </button>
      </label>

      <div v-if="loading && tracks.length === 0" class="loading-state">
        <LoadingDots />
        <span>正在加载</span>
      </div>
      <button v-else-if="error && tracks.length === 0" class="error-state" type="button" @click="reload">
        {{ error }}
      </button>
      <div v-else-if="filteredTracks.length" class="result-list">
        <TrackRow
          v-for="(track, index) in filteredTracks"
          :key="track.trackId ?? `${track.bvid}:${track.cid ?? index}`"
          :track="track"
          :index="index"
          :is-current="isCurrent(track)"
          :is-playing="isPlaying && isCurrent(track)"
          :is-liked="library.isTrackLiked(track)"
          @play="player.playList(filteredTracks, index)"
          @like="library.toggleLike(track)"
          @enqueue="player.enqueue(track)"
        />
      </div>
      <EmptyState
        v-else-if="tracks.length"
        title="没有匹配的稿件"
        description="换个标题、UP 或 BV 号试试"
      />
      <EmptyState v-else title="暂无稿件" description="这个 UP 主暂时没有可展示的视频" />

      <div ref="sentinel" class="load-more-zone">
        <div v-if="loadingMore" class="loading-more">
          <LoadingDots />
          <span>正在加载更多</span>
        </div>
        <button v-else-if="moreError" class="load-more-btn error" type="button" @click="loadMore(true)">
          {{ moreError }}
        </button>
        <button v-else-if="hasMore" class="load-more-btn" type="button" @click="loadMore()">
          加载更多
        </button>
        <span v-else-if="tracks.length >= pageSize" class="end-state">没有更多了</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { fetchBiliUpProfile, fetchBiliUpTracks, getTrackDetail, mediaUrl } from '@/api/client'
import { useLibraryStore } from '@/stores/libraryStore'
import { usePlayerStore } from '@/stores/playerStore'
import type { BiliUpProfile, Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import EmptyState from '@/components/base/EmptyState.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import TrackRow from '@/components/TrackRow.vue'

type UpOrder = 'pubdate' | 'click'

const route = useRoute()
const player = usePlayerStore()
const library = useLibraryStore()
const { currentTrack, isPlaying } = storeToRefs(player)

const pageSize = 20
const orderOptions: Array<{ value: UpOrder; label: string }> = [
  { value: 'pubdate', label: '时间' },
  { value: 'click', label: '热度' },
]

const profile = ref<BiliUpProfile | null>(null)
const tracks = ref<Track[]>([])
const query = ref('')
const order = ref<UpOrder>('pubdate')
const total = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref<string | null>(null)
const moreError = ref<string | null>(null)
const sentinel = ref<HTMLElement | null>(null)

let page = 1
let seq = 0
let observer: IntersectionObserver | null = null
let shellScrollEl: HTMLElement | null = null

const filteredTracks = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return tracks.value
  return tracks.value.filter((track) => matchesTrack(track, keyword))
})

function normalizedMid(value: unknown): number | null {
  const mid = Number(value)
  return Number.isFinite(mid) && mid > 0 ? mid : null
}

async function resolveCurrentMid(): Promise<number | null> {
  const directMid = normalizedMid(route.params.mid)
  if (directMid) return directMid

  const queryMid = normalizedMid(route.query.mid)
  if (queryMid) return queryMid

  const bvid = String(route.params.bvid || '').trim()
  if (!bvid) return null
  const cid = normalizedMid(route.query.cid)
  const detail = await getTrackDetail(bvid)
  return normalizedMid(detail.track.ownerMid)
    ?? normalizedMid(detail.pages.find((page) => cid !== null && page.cid === cid)?.ownerMid)
    ?? normalizedMid(detail.pages[0]?.ownerMid)
}

watch(
  () => [route.params.mid, route.params.bvid, route.query.cid, route.query.mid],
  () => {
    reset()
    void reload()
  },
  { immediate: true }
)

onMounted(() => {
  setupObserver()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

async function reload() {
  const currentSeq = ++seq
  loading.value = true
  error.value = null
  moreError.value = null
  page = 1
  try {
    const currentMid = await resolveCurrentMid()
    if (!currentMid) {
      throw new Error(route.query.owner ? `无法解析 UP 主：${route.query.owner}` : 'UP 主 ID 无效')
    }
    const [profileData, tracksData] = await Promise.all([
      fetchBiliUpProfile(currentMid),
      fetchBiliUpTracks(currentMid, 1, pageSize, order.value),
    ])
    if (currentSeq !== seq) return
    profile.value = tracksData.profile ?? profileData
    tracks.value = tracksData.tracks
    total.value = tracksData.total
    hasMore.value = tracksData.hasMore
  } catch (err) {
    if (currentSeq === seq) error.value = err instanceof Error ? err.message : 'UP 主稿件读取失败'
  } finally {
    if (currentSeq === seq) {
      loading.value = false
      await nextTick()
      setupObserver()
      maybeLoadMoreIfNeeded()
    }
  }
}

async function loadMore(force = false) {
  if (loading.value || loadingMore.value || !hasMore.value || (!force && moreError.value)) return

  const currentSeq = seq
  const nextPage = page + 1
  loadingMore.value = true
  moreError.value = null
  try {
    const currentMid = await resolveCurrentMid()
    if (!currentMid) return
    const data = await fetchBiliUpTracks(currentMid, nextPage, pageSize, order.value)
    if (currentSeq !== seq) return
    tracks.value = mergeTracks(tracks.value, data.tracks)
    total.value = data.total
    hasMore.value = data.hasMore && data.tracks.length > 0
    page = nextPage
  } catch (err) {
    if (currentSeq === seq) moreError.value = err instanceof Error ? err.message : '加载更多稿件失败'
  } finally {
    if (currentSeq === seq) {
      loadingMore.value = false
      await nextTick()
      maybeLoadMoreIfNeeded()
    }
  }
}

function setOrder(value: UpOrder) {
  if (order.value === value) return
  order.value = value
  reset()
  void reload()
}

function reset() {
  profile.value = null
  tracks.value = []
  query.value = ''
  total.value = 0
  hasMore.value = false
  loading.value = false
  loadingMore.value = false
  error.value = null
  moreError.value = null
  page = 1
  seq++
}

function isCurrent(track: Track): boolean {
  const current = currentTrack.value
  if (!current) return false
  if (current.trackId && track.trackId) return current.trackId === track.trackId
  if (current.cid != null && track.cid != null) return current.bvid === track.bvid && current.cid === track.cid
  return current.bvid === track.bvid
}

function mergeTracks(current: Track[], incoming: Track[]): Track[] {
  const seen = new Set(current.map(trackKey))
  const merged = [...current]
  for (const track of incoming) {
    const key = trackKey(track)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(track)
  }
  return merged
}

function trackKey(track: Track): string {
  return track.trackId ?? `${track.bvid}:${track.cid ?? 'main'}`
}

function matchesTrack(track: Track, keyword: string): boolean {
  return [
    track.title,
    track.owner,
    track.bvid,
    track.pageTitle ?? '',
  ].some((value) => value.toLowerCase().includes(keyword))
}

function setupObserver() {
  observer?.disconnect()
  observer = null
  shellScrollEl = document.querySelector('.shell-content') as HTMLElement | null
  if (!sentinel.value || typeof IntersectionObserver === 'undefined') return

  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        void loadMore()
      }
    },
    {
      root: shellScrollEl,
      rootMargin: '160px 0px',
      threshold: 0.01,
    }
  )
  observer.observe(sentinel.value)
}

function maybeLoadMoreIfNeeded() {
  const target = sentinel.value
  if (!target || !hasMore.value || loading.value || loadingMore.value || moreError.value) return
  const targetRect = target.getBoundingClientRect()
  const rootRect = shellScrollEl?.getBoundingClientRect()
  const bottom = rootRect?.bottom ?? window.innerHeight
  if (targetRect.top <= bottom + 160) {
    void loadMore()
  }
}
</script>

<style scoped>
.page {
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.up-head {
  display: flex;
  align-items: center;
  gap: 18px;
}

.up-avatar {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--color-bg-hover);
  flex-shrink: 0;
}

.up-avatar.fallback {
  display: grid;
  place-items: center;
  color: var(--color-text-tertiary);
}

.up-info {
  min-width: 0;
}

.kind {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.up-info h1 {
  margin-top: 4px;
  font-size: 26px;
  line-height: 1.2;
  color: var(--color-text-primary);
}

.up-sign {
  margin-top: 8px;
  max-width: 760px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.section-head h2 {
  font-size: 18px;
  color: var(--color-text-primary);
}

.section-head p {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.order-tabs {
  display: inline-flex;
  padding: 3px;
  border-radius: var(--radius-small);
  background: var(--color-bg-app);
  border: 1px solid var(--color-border);
}

.order-tab {
  height: 30px;
  padding: 0 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.order-tab.active {
  background: var(--color-primary);
  color: #fff;
}

.local-search {
  width: min(560px, 100%);
  height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  color: var(--color-text-tertiary);
}

.local-search input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  color: var(--color-text-primary);
  font-size: 13px;
}

.local-search button {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}

.local-search button:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.result-list {
  display: flex;
  flex-direction: column;
}

.loading-state,
.loading-more,
.load-more-zone,
.end-state {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.error-state,
.load-more-btn {
  min-height: 36px;
  align-self: center;
  padding: 0 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.error-state,
.load-more-btn.error {
  color: var(--color-primary);
}

@media (max-width: 720px) {
  .page {
    padding: 18px 16px;
  }

  .up-head,
  .section-head {
    align-items: flex-start;
  }

  .up-avatar {
    width: 72px;
    height: 72px;
  }
}
</style>
