<template>
  <div class="section-header">
    <div class="section-title">
      <span class="deco" aria-hidden="true">♪</span>
      <h2>{{ title }}</h2>
      <span v-if="count !== undefined" class="count">{{ count }}</span>
    </div>
    <div class="section-extra">
      <slot name="extra" />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  count?: number
}>()
</script>

<style scoped>
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.section-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.section-title h2 {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.deco {
  color: var(--color-primary);
  font-size: 14px;
}

.count {
  font-size: 13px;
  color: var(--color-text-tertiary);
}
</style>
