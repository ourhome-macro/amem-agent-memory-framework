<template>
  <div class="empty-state">
    <!-- 原创小电视助手：轻二次元占位插画 -->
    <svg class="tv" viewBox="0 0 120 110" fill="none" aria-hidden="true">
      <!-- 天线 -->
      <path d="M40 24 26 8" stroke="currentColor" stroke-width="5" stroke-linecap="round" />
      <path d="M80 24 94 8" stroke="currentColor" stroke-width="5" stroke-linecap="round" />
      <!-- 机身 -->
      <rect x="18" y="24" width="84" height="66" rx="14" fill="var(--color-primary-soft)" stroke="currentColor" stroke-width="4" />
      <!-- 屏幕 -->
      <rect x="30" y="36" width="60" height="42" rx="8" fill="var(--color-bg-content)" stroke="currentColor" stroke-width="3" />
      <!-- 眼睛 -->
      <circle cx="47" cy="55" r="4" fill="currentColor" />
      <circle cx="73" cy="55" r="4" fill="currentColor" />
      <!-- 腮红 -->
      <circle cx="40" cy="64" r="3" fill="var(--color-primary)" opacity="0.5" />
      <circle cx="80" cy="64" r="3" fill="var(--color-primary)" opacity="0.5" />
      <!-- 嘴 -->
      <path d="M55 63q5 4 10 0" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
    </svg>
    <p class="title">{{ title }}</p>
    <p v-if="description" class="desc">{{ description }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    description?: string
  }>(),
  { description: '' }
)
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 64px 24px;
  color: var(--color-text-tertiary);
  text-align: center;
}

.tv {
  width: 120px;
  height: 110px;
  color: var(--color-text-tertiary);
  opacity: 0.7;
}

.title {
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.desc {
  font-size: 13px;
  color: var(--color-text-tertiary);
  max-width: 320px;
  line-height: 1.6;
}
</style>
