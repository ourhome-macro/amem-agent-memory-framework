<template>
  <div class="track-card" @click="$emit('play')">
    <div class="cover-wrap">
      <img class="cover" :src="mediaUrl(track.cover)" :alt="track.title" loading="lazy" />
      <button v-if="removable" class="remove-btn" title="删除" @click.stop="$emit('remove')">
        <AppIcon name="close" :size="15" />
      </button>
      <button class="play-fab" title="播放" @click.stop="$emit('play')">
        <AppIcon name="play" :size="20" />
      </button>
      <span v-if="track.duration" class="duration">{{ formatDuration(track.duration) }}</span>
    </div>
    <div class="card-title" :title="track.title">{{ track.title }}</div>
    <div class="card-owner">{{ track.owner }}</div>
  </div>
</template>

<script setup lang="ts">
import type { Track } from '@/types'
import { mediaUrl } from '@/api/client'
import { formatDuration } from '@/utils/format'
import AppIcon from '@/components/base/AppIcon.vue'

withDefaults(defineProps<{ track: Track; removable?: boolean }>(), {
  removable: false,
})
defineEmits<{ play: []; remove: [] }>()
</script>

<style scoped>
.track-card {
  cursor: pointer;
}

.cover-wrap {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: var(--radius-medium);
  overflow: hidden;
  background: var(--color-bg-hover);
}

.cover {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 300ms ease;
}

.track-card:hover .cover {
  transform: scale(1.04);
}

.remove-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.54);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 160ms ease, background 160ms ease;
}

.track-card:hover .remove-btn,
.remove-btn:focus-visible {
  opacity: 1;
}

.remove-btn:hover {
  background: rgba(251, 114, 153, 0.92);
}

.play-fab {
  position: absolute;
  right: 12px;
  bottom: 12px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: none;
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 200ms ease, transform 200ms ease, background 160ms ease;
  box-shadow: 0 4px 12px rgba(251, 114, 153, 0.4);
}

.track-card:hover .play-fab {
  opacity: 1;
  transform: translateY(0);
}

.play-fab:hover {
  background: var(--color-primary-hover);
}

.duration {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.card-title {
  margin-top: 10px;
  font-size: 14px;
  color: var(--color-text-primary);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-owner {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

@media (prefers-reduced-motion: reduce) {
  .cover, .play-fab { transition: none; }
  .track-card:hover .cover { transform: none; }
}
</style>
