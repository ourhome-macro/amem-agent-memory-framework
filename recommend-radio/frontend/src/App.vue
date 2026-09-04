<template>
  <RouterView v-if="isAuthLayout" />
  <AppShell v-else />
  <Transition name="nowplaying">
    <NowPlayingView v-if="!isAuthLayout && ui.nowPlayingOpen" />
  </Transition>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import { useUiStore } from '@/stores/uiStore'
import { useAuthStore } from '@/stores/authStore'
import AppShell from '@/components/layout/AppShell.vue'
import NowPlayingView from '@/views/NowPlayingView.vue'

const route = useRoute()
const player = usePlayerStore()
const library = useLibraryStore()
const ui = useUiStore()
const auth = useAuthStore()

const isAuthLayout = computed(() => route.meta.layout === 'auth')

watch(
  [isAuthLayout, () => auth.appAuthenticated],
  () => initializeAppServices(),
  { immediate: true }
)

function initializeAppServices() {
  if (isAuthLayout.value || !auth.appAuthenticated) return
  void auth.initializeBili()
  void library.initialize()
  void player.initialize()
}
</script>

<style>
.nowplaying-enter-active,
.nowplaying-leave-active {
  transition: opacity 260ms ease, transform 260ms ease;
}
.nowplaying-enter-from,
.nowplaying-leave-to {
  opacity: 0;
  transform: translateY(24px);
}

@media (prefers-reduced-motion: reduce) {
  .nowplaying-enter-active,
  .nowplaying-leave-active {
    transition: opacity 160ms ease;
  }
  .nowplaying-enter-from,
  .nowplaying-leave-to {
    transform: none;
  }
}
</style>
