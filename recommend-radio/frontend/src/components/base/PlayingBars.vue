<template>
  <span class="playing-bars" :class="{ paused: !animated }" aria-hidden="true">
    <span></span>
    <span></span>
    <span></span>
    <span></span>
  </span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ animated?: boolean }>(), { animated: true })
</script>

<style scoped>
.playing-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 14px;
}

.playing-bars span {
  width: 2px;
  height: 100%;
  background: var(--color-primary);
  border-radius: 2px;
  transform-origin: bottom;
  animation: bounce 0.9s ease-in-out infinite;
}

.playing-bars span:nth-child(1) { animation-delay: -0.6s; }
.playing-bars span:nth-child(2) { animation-delay: -0.3s; }
.playing-bars span:nth-child(3) { animation-delay: -0.45s; }
.playing-bars span:nth-child(4) { animation-delay: -0.15s; }

.playing-bars.paused span {
  animation-play-state: paused;
  height: 40%;
}

@keyframes bounce {
  0%, 100% { transform: scaleY(0.35); }
  50% { transform: scaleY(1); }
}

@media (prefers-reduced-motion: reduce) {
  .playing-bars span { animation: none; height: 60%; }
}
</style>
