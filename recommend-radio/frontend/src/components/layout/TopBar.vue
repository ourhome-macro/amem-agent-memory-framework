<template>
  <header class="topbar">
    <button class="icon-btn mobile-nav-btn" title="打开导航" @click="emit('toggle-navigation')">
      <AppIcon name="menu" :size="19" />
    </button>
    <div class="nav-arrows">
      <button class="icon-btn" title="后退" @click="goBack">
        <AppIcon name="chevron" :size="18" style="transform: rotate(90deg)" />
      </button>
      <button class="icon-btn" title="前进" @click="goForward">
        <AppIcon name="chevron" :size="18" style="transform: rotate(-90deg)" />
      </button>
    </div>

    <form class="search-box" @submit.prevent="submitSearch" @focusout="handleSearchFocusOut">
      <AppIcon name="search" :size="16" class="search-icon" />
      <input
        v-model="keyword"
        type="text"
        placeholder="搜索，或粘贴 BV / 视频链接"
        class="search-input"
        autocomplete="off"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="showSearchHistory"
        aria-controls="topbar-search-history"
        :aria-activedescendant="activeHistoryId"
        @focus="openSearchHistory"
        @input="handleSearchInput"
        @keydown="handleSearchKeydown"
      />
      <SearchHistoryMenu
        v-if="showSearchHistory"
        :items="searchHistory"
        :active-index="activeHistoryIndex"
        menu-id="topbar-search-history"
        @select="selectHistoryItem"
        @remove="removeHistoryItem"
        @clear="clearSearchHistory"
        @hover="activeHistoryIndex = $event"
      />
    </form>

    <div class="actions">
      <button class="icon-btn theme-btn" :title="isDark ? '切换到浅色' : '切换到深色'" @click="ui.toggleTheme">
        <AppIcon :name="isDark ? 'sun' : 'moon'" :size="18" />
      </button>
      <button class="icon-btn notification-btn" title="消息">
        <AppIcon name="bell" :size="18" />
      </button>
      <button class="avatar" :title="biliAuthTitle" @click="openLogin">
        <img
          v-if="auth.biliUser?.face"
          class="avatar-img"
          :src="mediaUrl(auth.biliUser.face)"
          :alt="auth.biliUser.name"
        />
        <AppIcon v-else name="user" :size="16" class="bili-fallback-icon" />
        <span class="avatar-fallback">{{ auth.biliUser?.name || (auth.biliConnected ? 'B 站已连接' : '登录 B 站') }}</span>
      </button>

      <div ref="accountRoot" class="account-root" @keydown.esc="accountOpen = false">
        <button
          class="account-btn"
          :aria-expanded="accountOpen"
          aria-haspopup="menu"
          :title="`应用账户：${auth.appUser?.displayName ?? ''}`"
          @click="accountOpen = !accountOpen"
        >
          <AppIcon name="user" :size="17" />
          <span>{{ accountInitial }}</span>
        </button>
        <Transition name="account-menu">
          <div v-if="accountOpen" class="account-menu" role="menu">
            <div class="account-identity">
              <strong>{{ auth.appUser?.displayName }}</strong>
              <span>用户</span>
            </div>
            <button class="menu-item" role="menuitem" @click="logoutApp">
              <AppIcon name="logout" :size="16" />
              <span>退出应用账户</span>
            </button>
          </div>
        </Transition>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import { useUiStore } from '@/stores/uiStore'
import { useSearchHistory, useSearchHistoryMenu } from '@/composables/useSearchHistory'
import { mediaUrl } from '@/api/client'
import AppIcon from '@/components/base/AppIcon.vue'
import SearchHistoryMenu from '@/components/SearchHistoryMenu.vue'

const router = useRouter()
const route = useRoute()
const ui = useUiStore()
const auth = useAuthStore()
const emit = defineEmits<{ 'toggle-navigation': [] }>()
const { requestSearch } = useSearchHistory()

const keyword = ref('')
const accountOpen = ref(false)
const accountRoot = ref<HTMLElement | null>(null)
const isDark = computed(() => ui.theme === 'dark')
const biliAuthTitle = computed(() =>
  auth.biliConnected ? `B 站已登录：${auth.biliUser?.name ?? ''}` : '登录 B 站'
)
const accountInitial = computed(() => (auth.appUser?.displayName?.trim().slice(0, 1) || 'U').toUpperCase())
const {
  searchHistory,
  activeIndex: activeHistoryIndex,
  isVisible: showSearchHistory,
  activeOptionId: activeHistoryId,
  open: openSearchHistory,
  close: closeSearchHistory,
  handleInput: handleSearchInput,
  handleFocusOut: handleSearchFocusOut,
  handleKeydown: handleSearchKeydown,
  select: selectHistoryItem,
  remove: removeHistoryItem,
  clear: clearSearchHistory,
} = useSearchHistoryMenu(keyword, 'topbar-search-history', executeSearch)

onMounted(() => document.addEventListener('pointerdown', closeAccountMenu))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeAccountMenu))

function submitSearch() {
  const q = keyword.value.trim()
  if (!q) return
  executeSearch(q)
}

function executeSearch(value: string) {
  const q = value.trim()
  if (!q) return
  keyword.value = q
  closeSearchHistory()
  if (route.name === 'search' && String(route.query.q || '').trim() === q) {
    requestSearch(q)
    return
  }
  void router.push({ name: 'search', query: { q } })
}

function goBack() {
  router.back()
}

function goForward() {
  router.forward()
}

function openLogin() {
  router.push({ name: 'login' })
}

function closeAccountMenu(event: PointerEvent) {
  if (!accountRoot.value?.contains(event.target as Node)) accountOpen.value = false
}

async function logoutApp() {
  accountOpen.value = false
  await auth.logoutApp()
  window.location.reload()
}
</script>

<style scoped>
.topbar {
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
  background: var(--color-bg-content);
  border-bottom: 1px solid var(--color-border);
}

.nav-arrows {
  display: flex;
  gap: 4px;
}

.icon-btn.mobile-nav-btn {
  display: none;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: 50%;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}

.icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.search-box {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 320px;
  max-width: 40%;
  height: 36px;
  padding: 0 14px;
  background: var(--color-bg-app);
  border: 1px solid transparent;
  border-radius: 999px;
  transition: border-color 160ms ease, background 160ms ease;
}

.search-box:focus-within {
  border-color: var(--color-primary);
  background: var(--color-bg-content);
}

.search-icon {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  font-size: 13px;
  color: var(--color-text-primary);
}

.search-input::placeholder {
  color: var(--color-text-tertiary);
}

.actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar {
  height: 32px;
  max-width: 132px;
  min-width: 64px;
  padding: 0 12px 0 6px;
  border: 1px solid var(--color-border);
  background: transparent;
  border-radius: 999px;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 160ms ease, color 160ms ease;
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.avatar-img {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}

.avatar-fallback {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bili-fallback-icon {
  display: none;
}

.account-root {
  position: relative;
}

.account-btn {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: var(--color-bg-content);
  color: var(--color-text-secondary);
}

.account-btn span {
  display: none;
}

.account-btn:hover,
.account-btn[aria-expanded='true'] {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.account-menu {
  position: absolute;
  z-index: 210;
  top: calc(100% + 10px);
  right: 0;
  width: 220px;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  box-shadow: var(--shadow-popup);
}

.account-identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px 10px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 4px;
}

.account-identity strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-primary);
}

.account-identity span {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.menu-item {
  width: 100%;
  height: 36px;
  padding: 0 10px;
  display: flex;
  align-items: center;
  gap: 9px;
  border-radius: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.menu-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.account-menu-enter-active,
.account-menu-leave-active {
  transition: opacity 140ms ease, transform 140ms ease;
}

.account-menu-enter-from,
.account-menu-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

@media (max-width: 720px) {
  .topbar {
    gap: 6px;
    padding: 0 10px;
  }

  .icon-btn.mobile-nav-btn {
    display: flex;
    flex: 0 0 34px;
  }

  .nav-arrows,
  .theme-btn,
  .notification-btn {
    display: none;
  }

  .search-box {
    width: auto;
    max-width: none;
    min-width: 0;
    flex: 1;
  }

  .actions {
    gap: 4px;
  }

  .avatar {
    width: 34px;
    min-width: 34px;
    padding: 4px;
    justify-content: center;
  }

  .avatar-fallback {
    display: none;
  }

  .bili-fallback-icon {
    display: block;
  }
}

</style>
