import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

type Theme = 'light' | 'dark'

const THEME_KEY = 'bili-radio:theme'

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

function detectReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

export const useUiStore = defineStore('ui', () => {
  const stored = (localStorage.getItem(THEME_KEY) as Theme | null) ?? 'light'
  const theme = ref<Theme>(stored)
  const queueOpen = ref(false)
  const nowPlayingOpen = ref(false)
  const reducedMotion = ref(detectReducedMotion())

  applyTheme(theme.value)

  watch(theme, (value) => {
    applyTheme(value)
    localStorage.setItem(THEME_KEY, value)
  })

  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }

  function toggleQueue() {
    queueOpen.value = !queueOpen.value
  }

  function openNowPlaying() {
    nowPlayingOpen.value = true
  }

  function closeNowPlaying() {
    nowPlayingOpen.value = false
  }

  return {
    theme,
    queueOpen,
    nowPlayingOpen,
    reducedMotion,
    toggleTheme,
    toggleQueue,
    openNowPlaying,
    closeNowPlaying,
  }
})
