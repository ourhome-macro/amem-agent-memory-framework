<template>
  <div class="skeleton-list">
    <div v-for="n in count" :key="n" class="skeleton-row">
      <div class="sk sk-cover" />
      <div class="sk-lines">
        <div class="sk sk-line" style="width: 60%" />
        <div class="sk sk-line" style="width: 35%" />
      </div>
      <div class="sk sk-short" />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ count?: number }>(), { count: 6 })
</script>

<style scoped>
.skeleton-list {
  display: flex;
  flex-direction: column;
}

.skeleton-row {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 64px;
  padding: 0 12px;
}

.sk {
  background: var(--color-bg-hover);
  border-radius: var(--radius-small);
  position: relative;
  overflow: hidden;
}

.sk::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(255, 255, 255, 0.35),
    transparent
  );
  transform: translateX(-100%);
  animation: shimmer 1.4s infinite;
}

[data-theme='dark'] .sk::after {
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.06), transparent);
}

.sk-cover {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
}

.sk-lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sk-line {
  height: 12px;
}

.sk-short {
  width: 48px;
  height: 12px;
}

@keyframes shimmer {
  100% { transform: translateX(100%); }
}

@media (prefers-reduced-motion: reduce) {
  .sk::after { animation: none; }
}
</style>
