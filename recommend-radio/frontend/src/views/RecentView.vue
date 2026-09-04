<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>最近播放</h1>
        <p class="sub">{{ recent.length }} 条记录</p>
      </div>
      <button v-if="recent.length" class="ghost-btn" @click="clear">
        <AppIcon name="trash" :size="16" />
        <span>清空</span>
      </button>
    </div>

    <div v-if="recent.length" class="result-list">
      <TrackRow
        v-for="(t, i) in recent"
        :key="t.trackId ?? `${t.bvid}:${t.cid ?? i}`"
        :track="t"
        :index="i"
        :is-current="isCurrent(t)"
        :is-playing="isPlaying && isCurrent(t)"
        :is-liked="library.isLiked(t.bvid)"
        removable
        @play="player.playTrack(t)"
        @like="library.toggleLike(t)"
        @enqueue="player.enqueue(t)"
        @remove="library.removeRecent(t)"
      />
    </div>
    <EmptyState
      v-else
      title="还没有播放记录"
      description="播放过的内容会出现在这里，方便你再听一遍"
    />
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import type { Track } from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'
import TrackRow from '@/components/TrackRow.vue'
import EmptyState from '@/components/base/EmptyState.vue'

const player = usePlayerStore()
const library = useLibraryStore()
const { recent } = storeToRefs(library)
const { currentTrack, isPlaying } = storeToRefs(player)

function isCurrent(track: Track): boolean {
  const current = currentTrack.value
  if (!current) return false
  if (current.trackId && track.trackId) return current.trackId === track.trackId
  if (current.cid != null && track.cid != null) return current.bvid === track.bvid && current.cid === track.cid
  return current.bvid === track.bvid
}

function clear() {
  if (window.confirm('确定清空最近播放记录？')) {
    library.clearRecent()
  }
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
  align-items: flex-end;
  justify-content: space-between;
}

.page-head h1 {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.ghost-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: 17px;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 160ms ease, color 160ms ease;
}

.ghost-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.result-list {
  display: flex;
  flex-direction: column;
}
</style>
