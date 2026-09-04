<template>
  <div class="volume-control">
    <button
      class="volume-btn"
      :title="isMuted ? '取消静音' : '静音'"
      @click="toggleMute"
    >
      <AppIcon :name="volumeIcon" :size="18" />
    </button>

    <div class="volume-slider-container">
      <input
        type="range"
        min="0"
        max="100"
        :value="volumePercent"
        @input="handleVolumeChange"
        class="volume-slider"
        :style="{ '--fill': `${volumePercent}%` }"
        aria-label="音量"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { usePlayerStore } from '@/stores/playerStore'
import { storeToRefs } from 'pinia'
import AppIcon from '@/components/base/AppIcon.vue'

const store = usePlayerStore()
const { volume, isMuted } = storeToRefs(store)
const { setVolume, toggleMute } = store

const volumePercent = computed(() => (isMuted.value ? 0 : Math.round(volume.value * 100)))

const volumeIcon = computed(() => {
  if (isMuted.value || volume.value === 0) return 'volume-mute'
  if (volume.value < 0.5) return 'volume-low'
  return 'volume-high'
})

function handleVolumeChange(event: Event) {
  const target = event.target as HTMLInputElement
  setVolume(parseInt(target.value) / 100)
}
</script>

<style scoped>
.volume-control {
  display: flex;
  align-items: center;
  gap: 6px;
}

.volume-btn {
  width: 34px;
  height: 34px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: background 160ms ease, color 160ms ease;
}

.volume-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.volume-slider-container {
  width: 84px;
  display: flex;
  align-items: center;
}

.volume-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 4px;
  border-radius: 4px;
  outline: none;
  cursor: pointer;
  background: linear-gradient(
    to right,
    var(--color-primary) 0%,
    var(--color-primary) var(--fill, 80%),
    var(--color-border) var(--fill, 80%),
    var(--color-border) 100%
  );
}

.volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 12px;
  height: 12px;
  background: var(--color-primary);
  border-radius: 50%;
  cursor: pointer;
  transition: transform 160ms ease;
}

.volume-slider:hover::-webkit-slider-thumb {
  transform: scale(1.2);
}

.volume-slider::-moz-range-thumb {
  width: 12px;
  height: 12px;
  background: var(--color-primary);
  border: none;
  border-radius: 50%;
  cursor: pointer;
}
</style>
