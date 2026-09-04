<template>
  <div class="history-menu">
    <div class="history-head">
      <span>最近搜索</span>
      <button type="button" @pointerdown.prevent @click="emit('clear')">清空</button>
    </div>
    <ul :id="menuId" role="listbox" aria-label="最近搜索">
      <li
        v-for="(item, index) in items"
        :key="item"
        :class="{ active: index === activeIndex }"
        @mouseenter="emit('hover', index)"
      >
        <button
          :id="`${menuId}-option-${index}`"
          type="button"
          class="history-option"
          role="option"
          :aria-selected="index === activeIndex"
          @pointerdown.prevent
          @click="emit('select', item)"
        >
          <AppIcon name="clock" :size="15" />
          <span>{{ item }}</span>
        </button>
        <button
          type="button"
          class="remove-btn"
          :aria-label="`删除搜索记录：${item}`"
          title="删除记录"
          @pointerdown.prevent
          @click.stop="emit('remove', item)"
        >
          <AppIcon name="close" :size="14" />
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@/components/base/AppIcon.vue'

defineProps<{
  items: readonly string[]
  activeIndex: number
  menuId: string
}>()

const emit = defineEmits<{
  select: [value: string]
  remove: [value: string]
  clear: []
  hover: [index: number]
}>()
</script>

<style scoped>
.history-menu {
  position: absolute;
  z-index: 240;
  top: calc(100% + 8px);
  left: 0;
  width: 100%;
  min-width: 260px;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  box-shadow: var(--shadow-popup);
}

.history-head {
  height: 30px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.history-head button {
  padding: 4px 6px;
  border-radius: 5px;
  color: var(--color-text-secondary);
  font-size: 11px;
}

.history-head button:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

ul {
  max-height: 320px;
  overflow-y: auto;
}

li {
  height: 36px;
  display: flex;
  align-items: center;
  border-radius: 6px;
}

li:hover,
li.active {
  background: var(--color-bg-hover);
}

.history-option {
  min-width: 0;
  height: 100%;
  flex: 1;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--color-text-secondary);
  text-align: left;
}

.history-option span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

li.active .history-option,
.history-option:hover {
  color: var(--color-text-primary);
}

.remove-btn {
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--color-text-tertiary);
}

.remove-btn:hover {
  color: var(--color-primary);
}

@media (max-width: 720px) {
  .history-menu {
    min-width: min(320px, calc(100vw - 20px));
  }
}
</style>
