<template>
  <main class="session-error-page">
    <section class="session-error-state">
      <AppIcon name="activity" :size="34" />
      <h1>暂时无法连接服务</h1>
      <p>{{ auth.sessionError || '应用登录状态检查失败。' }}</p>
      <button class="primary-btn" :disabled="retrying" @click="retry">
        <AppIcon name="repeat" :size="16" />
        <span>{{ retrying ? '正在重试' : '重新连接' }}</span>
      </button>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import AppIcon from '@/components/base/AppIcon.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const retrying = ref(false)
let retryTimer: ReturnType<typeof setInterval> | null = null

const redirectTarget = computed(() => {
  const value = route.query.redirect
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
})

async function retry() {
  if (retrying.value) return
  retrying.value = true
  try {
    await auth.initializeSession(true)
    if (!auth.appAuthenticated) {
      auth.loginWithOidc(`${window.location.pathname}#${redirectTarget.value}`)
      return
    }
    await router.replace(redirectTarget.value)
  } catch {
    // The store keeps the server error for the recovery screen.
  } finally {
    retrying.value = false
  }
}

onMounted(() => {
  retryTimer = setInterval(() => void retry(), 3000)
})

onBeforeUnmount(() => {
  if (retryTimer) clearInterval(retryTimer)
})
</script>

<style scoped>
.session-error-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--color-bg-app);
}

.session-error-state {
  width: min(420px, 100%);
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--color-text-secondary);
  text-align: center;
}

.session-error-state > .app-icon {
  color: var(--color-primary);
}

.session-error-state h1 {
  color: var(--color-text-primary);
  font-size: 20px;
}

.session-error-state p {
  max-width: 360px;
  font-size: 13px;
}

.primary-btn {
  height: 36px;
  margin-top: 10px;
  padding: 0 16px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border-radius: var(--radius-small);
  background: var(--color-primary);
  color: #fff;
  font-size: 13px;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
