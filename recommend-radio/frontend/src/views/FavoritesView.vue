<template>
  <div class="page favorites">
    <header class="fav-header">
      <div>
        <h1>B 站收藏夹</h1>
        <p>读取当前已登录账号的收藏夹</p>
      </div>
      <button v-if="auth.biliConnected" class="ghost-btn" :disabled="folderLoading" @click="loadFolders">
        <AppIcon name="repeat" :size="16" />
        <span>刷新</span>
      </button>
    </header>

    <section v-if="!auth.biliConnected" class="login-required">
      <h2>需要登录 B 站</h2>
      <p>收藏夹属于账号数据，请先扫码登录。</p>
      <button class="primary-btn" @click="goLogin">去登录</button>
    </section>

    <template v-else-if="!activeFolderId">
      <div v-if="folderLoading" class="loading-state">
        <LoadingDots />
        <span>正在读取收藏夹</span>
      </div>
      <p v-else-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <template v-else>
        <label v-if="folders.length" class="local-search">
          <AppIcon name="search" :size="16" />
          <input v-model="folderQuery" type="search" placeholder="搜索收藏夹" />
          <button v-if="folderQuery" type="button" title="清空搜索" @click="folderQuery = ''">
            <AppIcon name="close" :size="14" />
          </button>
        </label>
        <div v-if="filteredFolders.length" class="folder-grid">
          <button
            v-for="folder in filteredFolders"
            :key="folder.mediaId"
            class="folder-card"
            @click="openFolder(folder.mediaId)"
          >
            <div class="folder-cover">
              <img v-if="folder.cover" :src="mediaUrl(folder.cover)" :alt="folder.title" loading="lazy" />
              <div v-else class="cover-placeholder">{{ folder.title.slice(0, 1) }}</div>
              <span class="folder-count">{{ folder.mediaCount }}</span>
            </div>
            <div class="folder-title">{{ folder.title }}</div>
          </button>
        </div>
        <p v-else class="empty-filter">没有匹配的收藏夹</p>
      </template>
    </template>

    <template v-else>
      <button class="back-btn" @click="closeFolder">
        <AppIcon name="chevron" :size="18" class="back-icon" />
        <span>全部收藏夹</span>
      </button>

      <div class="detail-top">
        <div class="detail-cover">
          <img v-if="activeCover" :src="mediaUrl(activeCover)" :alt="activeFolder?.title" />
          <div v-else class="cover-placeholder">{{ activeFolder?.title?.slice(0, 1) || 'F' }}</div>
        </div>
        <div class="detail-info">
          <span class="detail-kind">收藏夹</span>
          <h1 class="detail-title">{{ activeFolder?.title || `收藏夹 ${activeFolderId}` }}</h1>
          <p class="detail-meta">{{ activeFolder?.mediaCount ?? activeTracks.length }} 个内容</p>
          <div class="detail-actions">
            <button class="primary-btn" :disabled="activeTracks.length === 0" @click="playAll">
              <AppIcon name="play" :size="16" />
              <span>播放全部</span>
            </button>
            <button class="ghost-btn" :disabled="importing" @click="importAsPlaylist">
              <AppIcon name="import" :size="16" />
              <span>{{ importing ? '正在导入' : '导入为本地歌单' }}</span>
            </button>
          </div>
          <p v-if="notice" class="notice-text">{{ notice }}</p>
        </div>
      </div>

      <label v-if="activeTracks.length" class="local-search">
        <AppIcon name="search" :size="16" />
        <input v-model="trackQuery" type="search" placeholder="搜索当前收藏夹" />
        <button v-if="trackQuery" type="button" title="清空搜索" @click="trackQuery = ''">
          <AppIcon name="close" :size="14" />
        </button>
      </label>

      <div v-if="trackLoading" class="loading-state">
        <LoadingDots />
        <span>正在读取收藏夹内容</span>
      </div>
      <p v-else-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <div v-else-if="filteredActiveTracks.length" class="result-list">
        <TrackRow
          v-for="(track, i) in filteredActiveTracks"
          :key="track.trackId ?? `${track.bvid}:${track.cid ?? i}`"
          :track="track"
          :index="i"
          :is-current="isCurrent(track)"
          :is-playing="isPlaying && isCurrent(track)"
          :is-liked="library.isLiked(track.bvid)"
          @play="player.playList(filteredActiveTracks, i)"
          @like="library.toggleLike(track)"
          @enqueue="player.enqueue(track)"
        />
      </div>
      <p v-else class="empty-filter">没有匹配的收藏内容</p>
      <div v-if="favoriteHasMore" class="load-more-row">
        <button class="ghost-btn" :disabled="loadingMore" @click="loadMoreTracks">
          <AppIcon name="repeat" :size="16" />
          <span>{{ loadingMore ? '正在加载' : '加载更多' }}</span>
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  fetchBiliFavoriteFolders,
  fetchBiliFavoriteTracks,
  importBiliFavoriteAsPlaylist,
  mediaUrl,
} from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { usePlayerStore } from '@/stores/playerStore'
import type { FavoriteFolder, Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import TrackRow from '@/components/TrackRow.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const player = usePlayerStore()
const library = useLibraryStore()
const { currentTrack, isPlaying } = storeToRefs(player)

const folders = ref<FavoriteFolder[]>([])
const activeFolderDetail = ref<FavoriteFolder | null>(null)
const activeTracks = ref<Track[]>([])
const folderQuery = ref('')
const trackQuery = ref('')
const folderLoading = ref(false)
const trackLoading = ref(false)
const importing = ref(false)
const errorMessage = ref<string | null>(null)
const notice = ref<string | null>(null)
const activePage = ref(1)
const favoriteHasMore = ref(false)
const loadingMore = ref(false)

const activeFolderId = computed(() => {
  const raw = route.query.folder
  const value = typeof raw === 'string' ? Number(raw) : 0
  return Number.isFinite(value) && value > 0 ? value : 0
})

const activeFolder = computed(() => {
  return activeFolderDetail.value ?? folders.value.find((folder) => folder.mediaId === activeFolderId.value)
})

const activeCover = computed(() => activeFolder.value?.cover || activeTracks.value[0]?.cover || '')
const filteredFolders = computed(() => {
  const keyword = folderQuery.value.trim().toLowerCase()
  if (!keyword) return folders.value
  return folders.value.filter((folder) => [
    folder.title,
    String(folder.mediaId),
  ].some((value) => value.toLowerCase().includes(keyword)))
})
const filteredActiveTracks = computed(() => {
  const keyword = trackQuery.value.trim().toLowerCase()
  if (!keyword) return activeTracks.value
  return activeTracks.value.filter((track) => matchesTrack(track, keyword))
})

watch(
  () => activeFolderId.value,
  (id) => {
    notice.value = null
    if (id) {
      void loadFolderTracks(id)
    } else {
      activeFolderDetail.value = null
      activeTracks.value = []
      trackQuery.value = ''
      activePage.value = 1
      favoriteHasMore.value = false
    }
  },
  { immediate: true }
)

onMounted(async () => {
  await auth.initializeBili()
  if (auth.biliConnected) {
    await loadFolders()
  }
})

async function loadFolders() {
  folderLoading.value = true
  errorMessage.value = null
  try {
    folders.value = await fetchBiliFavoriteFolders()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '收藏夹读取失败'
  } finally {
    folderLoading.value = false
  }
}

async function loadFolderTracks(mediaId: number) {
  if (!auth.biliConnected) return
  trackLoading.value = true
  errorMessage.value = null
  activePage.value = 1
  favoriteHasMore.value = false
  try {
    const result = await fetchBiliFavoriteTracks(mediaId, 1, 20)
    activeFolderDetail.value = result.folder
    activeTracks.value = result.tracks
    favoriteHasMore.value = result.hasMore
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '收藏夹内容读取失败'
  } finally {
    trackLoading.value = false
  }
}

async function loadMoreTracks() {
  if (!activeFolderId.value || loadingMore.value || !favoriteHasMore.value) return
  loadingMore.value = true
  errorMessage.value = null
  try {
    const nextPage = activePage.value + 1
    const result = await fetchBiliFavoriteTracks(activeFolderId.value, nextPage, 20)
    activeFolderDetail.value = result.folder
    activeTracks.value = mergeTracks(activeTracks.value, result.tracks)
    activePage.value = nextPage
    favoriteHasMore.value = result.hasMore
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '收藏夹继续加载失败'
  } finally {
    loadingMore.value = false
  }
}

function openFolder(mediaId: number) {
  router.push({ path: '/favorites', query: { folder: String(mediaId) } })
}

function closeFolder() {
  router.push({ path: '/favorites' })
}

function goLogin() {
  router.push({ name: 'login', query: { redirect: route.fullPath } })
}

function isCurrent(track: Track): boolean {
  const current = currentTrack.value
  if (!current) return false
  if (current.trackId && track.trackId) return current.trackId === track.trackId
  if (current.cid != null && track.cid != null) return current.bvid === track.bvid && current.cid === track.cid
  return current.bvid === track.bvid
}

function mergeTracks(current: Track[], incoming: Track[]): Track[] {
  const result = [...current]
  for (const track of incoming) {
    if (!result.some((item) => isSameTrack(item, track))) {
      result.push(track)
    }
  }
  return result
}

function isSameTrack(a: Track, b: Track): boolean {
  if (a.trackId && b.trackId) return a.trackId === b.trackId
  if (a.cid != null || b.cid != null) return a.bvid === b.bvid && a.cid != null && b.cid != null && a.cid === b.cid
  return a.bvid === b.bvid
}

function playAll() {
  if (filteredActiveTracks.value.length) player.playList(filteredActiveTracks.value, 0)
}

function matchesTrack(track: Track, keyword: string): boolean {
  return [
    track.title,
    track.owner,
    track.bvid,
    track.pageTitle ?? '',
  ].some((value) => value.toLowerCase().includes(keyword))
}

async function importAsPlaylist() {
  const folder = activeFolder.value
  if (!folder || importing.value) return
  importing.value = true
  notice.value = null
  errorMessage.value = null
  try {
    const result = await importBiliFavoriteAsPlaylist(folder.mediaId, folder.title)
    await library.refreshFromBackend()
    notice.value = `已导入 ${result.import.added} 首，重复 ${result.import.duplicated} 首，不可用 ${result.import.unavailable} 首`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入失败'
  } finally {
    importing.value = false
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

.fav-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.fav-header h1 {
  font-size: 26px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.fav-header p {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.login-required {
  max-width: 420px;
  padding: 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-medium);
  background: var(--color-bg-content);
}

.login-required h2 {
  font-size: 18px;
  color: var(--color-text-primary);
}

.login-required p {
  margin: 8px 0 18px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.folder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
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

.folder-card {
  text-align: left;
}

.folder-cover {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: var(--radius-medium);
  overflow: hidden;
  background: var(--color-bg-hover);
}

.folder-cover img,
.detail-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.folder-card:hover .folder-cover img {
  transform: scale(1.04);
  transition: transform 300ms ease;
}

.cover-placeholder {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 28px;
  font-weight: 700;
}

.folder-count {
  position: absolute;
  right: 8px;
  top: 8px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
}

.folder-title {
  margin-top: 10px;
  font-size: 14px;
  color: var(--color-text-primary);
}

.back-btn {
  width: fit-content;
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--color-text-secondary);
  font-size: 14px;
  padding: 6px 10px;
  border-radius: var(--radius-small);
  transition: background 160ms ease, color 160ms ease;
}

.back-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.back-icon {
  transform: rotate(90deg);
}

.detail-top {
  display: flex;
  gap: 24px;
  align-items: flex-end;
}

.detail-cover {
  width: 180px;
  height: 180px;
  border-radius: var(--radius-large);
  overflow: hidden;
  background: var(--color-bg-hover);
  box-shadow: var(--shadow-popup);
  flex-shrink: 0;
}

.detail-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.detail-kind {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.detail-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.detail-meta,
.notice-text {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.detail-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.primary-btn,
.ghost-btn {
  height: 38px;
  padding: 0 18px;
  border-radius: var(--radius-small);
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
}

.primary-btn {
  background: var(--color-primary);
  color: #fff;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.ghost-btn {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-primary);
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.primary-btn:disabled,
.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.result-list {
  display: flex;
  flex-direction: column;
}

.load-more-row {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
}

.loading-state {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.error-text {
  color: var(--color-primary);
  font-size: 13px;
}

.empty-filter {
  color: var(--color-text-secondary);
  font-size: 13px;
}

@media (max-width: 720px) {
  .page {
    padding: 20px;
  }

  .detail-top {
    align-items: flex-start;
  }

  .detail-cover {
    width: 112px;
    height: 112px;
  }
}
</style>
