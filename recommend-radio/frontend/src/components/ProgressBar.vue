<template>
  <div
    class="progress"
    ref="progressRef"
    @mousedown="startDrag"
    @click="handleClick"
  >
    <div class="track">
      <div class="buffer" :style="{ width: `${bufferPercent}%` }"></div>
      <div class="fill" :style="{ width: `${displayProgress}%` }"></div>
    </div>
    <div class="thumb" :style="{ left: `${displayProgress}%` }"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { usePlayerStore } from '@/stores/playerStore'
import { storeToRefs } from 'pinia'

const store = usePlayerStore()
const { progress, bufferPercent, duration } = storeToRefs(store)
const { seek } = store

const progressRef = ref<HTMLElement | null>(null)
const dragging = ref(false)
const dragPercent = ref(0)

// 拖动时用本地百分比，避免和实时进度打架
const displayProgress = computed(() => (dragging.value ? dragPercent.value : progress.value))

function percentFromEvent(event: MouseEvent): number {
  if (!progressRef.value) return 0
  const rect = progressRef.value.getBoundingClientRect()
  const x = event.clientX - rect.left
  return Math.max(0, Math.min(1, x / rect.width)) * 100
}

function handleClick(event: MouseEvent) {
  if (duration.value === 0) return
  const percent = percentFromEvent(event)
  seek((percent / 100) * duration.value)
}

function startDrag(event: MouseEvent) {
  if (duration.value === 0) return
  dragging.value = true
  dragPercent.value = percentFromEvent(event)
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}

function onDrag(event: MouseEvent) {
  dragPercent.value = percentFromEvent(event)
}

function endDrag() {
  if (dragging.value && duration.value > 0) {
    seek((dragPercent.value / 100) * duration.value)
  }
  dragging.value = false
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}
</script>

<style scoped>
.progress {
  flex: 1;
  height: 16px;
  position: relative;
  display: flex;
  align-items: center;
  cursor: pointer;
}

.track {
  width: 100%;
  height: 4px;
  border-radius: 4px;
  background: var(--color-border);
  position: relative;
  overflow: hidden;
}

.buffer {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: var(--color-text-tertiary);
  opacity: 0.35;
  border-radius: 4px;
}

.fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: var(--color-primary);
  border-radius: 4px;
}

.thumb {
  position: absolute;
  top: 50%;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--color-primary);
  transform: translate(-50%, -50%) scale(0);
  transition: transform 120ms ease;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
  pointer-events: none;
}

.progress:hover .thumb {
  transform: translate(-50%, -50%) scale(1);
}
</style>
