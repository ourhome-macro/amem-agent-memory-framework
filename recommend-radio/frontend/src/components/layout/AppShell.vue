<template>
  <div class="app-shell">
    <Sidebar
      class="shell-sidebar"
      :class="{ 'mobile-open': mobileSidebarOpen }"
      @navigate="mobileSidebarOpen = false"
    />
    <button
      v-if="mobileSidebarOpen"
      class="mobile-backdrop"
      aria-label="关闭导航"
      @click="mobileSidebarOpen = false"
    />
    <TopBar class="shell-topbar" @toggle-navigation="mobileSidebarOpen = !mobileSidebarOpen" />
    <main class="shell-content">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
    <PlayerBar class="shell-player" />
    <QueueDrawer />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView } from 'vue-router'
import Sidebar from '@/components/layout/Sidebar.vue'
import TopBar from '@/components/layout/TopBar.vue'
import PlayerBar from '@/components/layout/PlayerBar.vue'
import QueueDrawer from '@/components/layout/QueueDrawer.vue'

const mobileSidebarOpen = ref(false)

function closeOnEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') mobileSidebarOpen.value = false
}

onMounted(() => document.addEventListener('keydown', closeOnEscape))
onBeforeUnmount(() => document.removeEventListener('keydown', closeOnEscape))
</script>

<style scoped>
.app-shell {
  width: 100vw;
  height: 100vh;
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--topbar-height) 1fr var(--player-height);
  grid-template-areas:
    'sidebar topbar'
    'sidebar content'
    'player player';
  background: var(--color-bg-app);
  overflow: hidden;
}

.shell-sidebar {
  grid-area: sidebar;
}

.shell-topbar {
  grid-area: topbar;
  min-width: 0;
}

.shell-content {
  grid-area: content;
  overflow-y: auto;
  background: var(--color-bg-app);
}

.shell-player {
  grid-area: player;
}

.mobile-backdrop {
  display: none;
}

/* 页面切换过渡 */
.page-enter-active,
.page-leave-active {
  transition: opacity 180ms ease;
}
.page-enter-from,
.page-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active {
    transition: none;
  }
}

@media (max-width: 720px) {
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-areas:
      'topbar'
      'content'
      'player';
  }

  .shell-sidebar {
    position: fixed;
    z-index: 260;
    inset: 0 auto var(--player-height) 0;
    width: min(280px, calc(100vw - 48px));
    height: auto;
    transform: translateX(-100%);
    transition: transform 180ms ease;
  }

  .shell-sidebar.mobile-open {
    transform: translateX(0);
  }

  .mobile-backdrop {
    position: fixed;
    z-index: 250;
    inset: 0 0 var(--player-height);
    display: block;
    width: 100%;
    background: rgba(12, 12, 16, 0.46);
    border: 0;
  }
}
</style>
