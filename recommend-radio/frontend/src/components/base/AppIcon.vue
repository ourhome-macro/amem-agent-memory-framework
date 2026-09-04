<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in paths" :key="i" :d="d" />
    <template v-if="name === 'play'">
      <polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'star-filled'">
      <path
        d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"
        fill="currentColor"
        stroke="none"
      />
    </template>
    <template v-else-if="name === 'volume-high' || name === 'volume-low' || name === 'volume-mute'">
      <polygon points="4 9 4 15 8 15 13 20 13 4 8 9 4 9" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'heart-filled'">
      <path
        d="M12 20s-7-4.3-9.2-8.4C1 8.3 2.8 5 6 5c2 0 3.2 1.2 4 2.3C10.8 6.2 12 5 14 5c3.2 0 5 3.3 3.2 6.6C19 15.7 12 20 12 20z"
        fill="currentColor"
        stroke="none"
      />
    </template>
    <circle
      v-for="(c, i) in circles"
      :key="`c${i}`"
      :cx="c.cx"
      :cy="c.cy"
      :r="c.r"
      :fill="c.fill || 'none'"
    />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name: string
    size?: number | string
  }>(),
  { size: 20 }
)

type IconDef = { paths: string[]; circles?: { cx: number; cy: number; r: number; fill?: string }[] }

const ICONS: Record<string, IconDef> = {
  home: { paths: ['M3 10.5 12 3l9 7.5', 'M5 9.5V21h14V9.5'] },
  search: { paths: ['M21 21l-4.3-4.3'], circles: [{ cx: 11, cy: 11, r: 7 }] },
  message: { paths: ['M21 12a8 8 0 0 1-8 8H7l-4 3v-6.2A8 8 0 1 1 21 12z'] },
  send: { paths: ['M22 2 11 13', 'M22 2 15 22l-4-9-9-4 20-7z'] },
  check: { paths: ['M20 6 9 17l-5-5'] },
  compass: {
    paths: ['M15.5 8.5 13 13l-4.5 2.5L11 11l4.5-2.5z'],
    circles: [{ cx: 12, cy: 12, r: 9 }],
  },
  star: { paths: ['M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z'] },
  list: { paths: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3 6h.01', 'M3 12h.01', 'M3 18h.01'] },
  clock: { paths: ['M12 7v5l3 2'], circles: [{ cx: 12, cy: 12, r: 9 }] },
  heart: {
    paths: ['M12 20s-7-4.3-9.2-8.4C1 8.3 2.8 5 6 5c2 0 3.2 1.2 4 2.3C10.8 6.2 12 5 14 5c3.2 0 5 3.3 3.2 6.6C19 15.7 12 20 12 20z'],
  },
  'heart-filled': {
    paths: [],
  },
  pause: { paths: ['M8 5v14', 'M16 5v14'] },
  'skip-back': { paths: ['M6 5v14', 'M6 12l10-7v14z'] },
  'skip-forward': { paths: ['M18 5v14', 'M18 12 8 5v14z'] },
  'volume-high': { paths: ['M16 8a5 5 0 0 1 0 8', 'M18.5 5.5a9 9 0 0 1 0 13'] },
  'volume-low': { paths: ['M16 8a5 5 0 0 1 0 8'] },
  'volume-mute': { paths: ['M22 9l-6 6', 'M16 9l6 6'] },
  queue: { paths: ['M4 6h11', 'M4 12h11', 'M4 18h8', 'M17 14v6', 'M21 12l-4 2 4 2v-4z'] },
  disc: { paths: [], circles: [{ cx: 12, cy: 12, r: 9 }, { cx: 12, cy: 12, r: 2.5, fill: 'currentColor' }] },
  chevron: { paths: ['M6 9l6 6 6-6'] },
  'chevron-down': { paths: ['M6 9l6 6 6-6'] },
  close: { paths: ['M6 6l12 12', 'M18 6 6 18'] },
  moon: { paths: ['M21 12.8A8.5 8.5 0 1 1 11.2 3a6.5 6.5 0 0 0 9.8 9.8z'] },
  sun: {
    paths: ['M12 2v2', 'M12 20v2', 'M4.9 4.9l1.4 1.4', 'M17.7 17.7l1.4 1.4', 'M2 12h2', 'M20 12h2', 'M4.9 19.1l1.4-1.4', 'M17.7 6.3l1.4-1.4'],
    circles: [{ cx: 12, cy: 12, r: 4 }],
  },
  bell: { paths: ['M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'] },
  shuffle: { paths: ['M16 4h4v4', 'M4 20 20 4', 'M16 20h4v-4', 'M4 4l5 5', 'M14 14l6 6'] },
  repeat: { paths: ['M17 2l4 4-4 4', 'M3 12v-2a4 4 0 0 1 4-4h14', 'M7 22l-4-4 4-4', 'M21 12v2a4 4 0 0 1-4 4H3'] },
  undo: { paths: ['M9 14 4 9l5-5', 'M4 9h10a6 6 0 0 1 0 12h-3'] },
  'repeat-one': { paths: ['M17 2l4 4-4 4', 'M3 12v-2a4 4 0 0 1 4-4h14', 'M7 22l-4-4 4-4', 'M21 12v2a4 4 0 0 1-4 4H3', 'M11 15v-4l-1 1'] },
  trash: { paths: ['M4 7h16', 'M9 7V5h6v2', 'M6 7l1 13h10l1-13'] },
  plus: { paths: ['M12 5v14', 'M5 12h14'] },
  import: { paths: ['M12 3v12', 'M8 11l4 4 4-4', 'M4 19h16'] },
  download: { paths: ['M12 3v12', 'M8 11l4 4 4-4', 'M5 21h14'] },
  fullscreen: { paths: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5'] },
  subtitle: { paths: ['M4 6h16v12H4z', 'M7 13h3', 'M13 13h4', 'M7 16h6'] },
  pip: { paths: ['M3 5h18v14H3z', 'M12 12h7v5h-7z'] },
  lock: { paths: ['M7 11V8a5 5 0 0 1 10 0v3', 'M6 11h12v10H6z'] },
  unlock: { paths: ['M7 11V8a5 5 0 0 1 9.2-2.8', 'M6 11h12v10H6z'] },
  more: { paths: [], circles: [{ cx: 5, cy: 12, r: 1.4, fill: 'currentColor' }, { cx: 12, cy: 12, r: 1.4, fill: 'currentColor' }, { cx: 19, cy: 12, r: 1.4, fill: 'currentColor' }] },
  shield: { paths: ['M12 3 20 6v5c0 5-3.4 8.6-8 10-4.6-1.4-8-5-8-10V6l8-3z', 'M9 12l2 2 4-4'] },
  user: { paths: ['M4 21a8 8 0 0 1 16 0'], circles: [{ cx: 12, cy: 7, r: 4 }] },
  logout: { paths: ['M10 4H5v16h5', 'M14 8l4 4-4 4', 'M8 12h10'] },
  activity: { paths: ['M3 12h4l2-6 4 12 2-6h6'] },
  'external-link': { paths: ['M14 4h6v6', 'M20 4l-9 9', 'M18 13v7H4V6h7'] },
  menu: { paths: ['M4 7h16', 'M4 12h16', 'M4 17h16'] },
}

const def = computed<IconDef>(() => ICONS[props.name] ?? { paths: [] })
const paths = computed(() => def.value.paths)
const circles = computed(() => def.value.circles ?? [])
</script>

<style scoped>
.app-icon {
  display: block;
  flex-shrink: 0;
}
</style>
