<template>
  <div class="page">
    <div class="page-head">
      <div class="head-icon">
        <AppIcon name="heart-filled" :size="30" />
      </div>
      <div>
        <span class="kind">我的音乐</span>
        <h1>我喜欢</h1>
        <p class="sub">{{ likes.length }} 首</p>
      </div>
      <button v-if="likes.length" class="primary-btn" @click="playAll">
        <AppIcon name="play" :size="16" />
        <span>播放全部</span>
      </button>
    </div>

    <label v-if="likes.length" class="local-search">
      <AppIcon name="search" :size="16" />
      <input v-model="query" type="search" placeholder="搜索我喜欢" />
      <button v-if="query" type="button" title="清空搜索" @click="query = ''">
        <AppIcon name="close" :size="14" />
      </button>
    </label>

    <div v-if="filteredLikes.length" class="result-list">
      <div
        v-for="item in filteredLikes"
        :key="item.track.trackId ?? `${item.track.bvid}:${item.track.cid ?? item.likeIndex}`"
        class="draggable-row"
        :data-like-index="item.likeIndex"
        :class="{ dragging: dragIndex === item.likeIndex, 'drop-target': dropIndex === item.likeIndex && dragIndex !== item.likeIndex }"
      >
        <span
          class="drag-handle"
          title="拖拽排序"
          role="button"
          tabindex="0"
          @pointerdown="startReorder(item.likeIndex, $event)"
        >
          ☰
        </span>
        <TrackRow
          :track="item.track"
          :index="item.likeIndex"
          :is-current="isCurrent(item.track)"
          :is-playing="isPlaying && isCurrent(item.track)"
          :is-liked="true"
          @play="playFiltered(item.likeIndex)"
          @like="library.toggleLike(item.track)"
          @enqueue="player.enqueue(item.track)"
        />
      </div>
    </div>
    <EmptyState
      v-else-if="likes.length"
      title="没有匹配的喜欢"
      description="换个标题、UP 或 BV 号试试"
    />
    <EmptyState
      v-else
      title="还没有喜欢的内容"
      description="点击任意曲目的爱心，就会收藏到这里"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { usePointerReorder } from '@/composables/usePointerReorder'
import type { Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import TrackRow from '@/components/TrackRow.vue'
import EmptyState from '@/components/base/EmptyState.vue'

const player = usePlayerStore()
const library = useLibraryStore()
const { likes } = storeToRefs(library)
const { currentTrack, isPlaying } = storeToRefs(player)
const query = ref('')
const { dragIndex, dropIndex, startReorder } = usePointerReorder({
  dataAttribute: 'data-like-index',
  onMove: (from, to) => library.moveLikeItem(from, to),
})

const filteredLikes = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return likes.value
    .map((track, likeIndex) => ({ track, likeIndex }))
    .filter((item) => !keyword || matchesTrack(item.track, keyword))
})

function isCurrent(track: Track): boolean {
  const current = currentTrack.value
  if (!current) return false
  if (current.trackId && track.trackId) return current.trackId === track.trackId
  if (current.cid != null && track.cid != null) return current.bvid === track.bvid && current.cid === track.cid
  return current.bvid === track.bvid
}

function playAll() {
  const tracks = filteredLikes.value.map((item) => item.track)
  if (tracks.length) player.playList(tracks, 0)
}

function playFiltered(likeIndex: number) {
  const startIndex = filteredLikes.value.findIndex((item) => item.likeIndex === likeIndex)
  const tracks = filteredLikes.value.map((item) => item.track)
  if (startIndex >= 0) player.playList(tracks, startIndex)
}

function matchesTrack(track: Track, keyword: string): boolean {
  return [
    track.title,
    track.owner,
    track.bvid,
    track.pageTitle ?? '',
  ].some((value) => value.toLowerCase().includes(keyword))
}
</script>

<style scoped>
.page {
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-head {
  display: flex;
  align-items: center;
  gap: 20px;
}

.local-search {
  width: min(520px, 100%);
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

.head-icon {
  width: 88px;
  height: 88px;
  border-radius: var(--radius-large);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  flex-shrink: 0;
}

.kind {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.page-head h1 {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-top: 2px;
}

.sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.primary-btn {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 20px;
  border: none;
  border-radius: 19px;
  background: var(--color-primary);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: background 160ms ease;
}

.primary-btn:hover {
  background: var(--color-primary-hover);
}

.result-list {
  display: flex;
  flex-direction: column;
}

.draggable-row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: center;
  border-radius: var(--radius-small);
}

.draggable-row.dragging {
  opacity: 0.55;
}

.draggable-row.drop-target {
  background: var(--color-primary-soft);
}

.drag-handle {
  display: grid;
  place-items: center;
  width: 26px;
  height: 64px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: grab;
  user-select: none;
  touch-action: none;
}

.drag-handle:active {
  cursor: grabbing;
}
</style>
