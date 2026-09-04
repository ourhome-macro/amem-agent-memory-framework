<template>
  <span class="loading-dots" :class="{ light }" aria-label="加载中">
    <span></span>
    <span></span>
    <span></span>
  </span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ light?: boolean }>(), { light: false })
</script>

<style scoped>
.loading-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.loading-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: danmaku 1.1s ease-in-out infinite;
}

.loading-dots.light span {
  background: #fff;
}

.loading-dots span:nth-child(2) { animation-delay: 0.15s; }
.loading-dots span:nth-child(3) { animation-delay: 0.3s; }

@keyframes danmaku {
  0%, 100% { transform: translateY(0); opacity: 0.4; }
  50% { transform: translateY(-6px); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .loading-dots span { animation: none; opacity: 0.7; }
}
</style>
