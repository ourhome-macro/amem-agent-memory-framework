<template>
  <main class="login-page">
    <section class="login-panel">
      <div class="brand">
        <img class="brand-logo" :src="iconUrl" alt="Bilibili Radio" />
        <div>
          <h1>Bilibili Radio</h1>
          <p>扫码登录 B 站账号</p>
        </div>
      </div>

      <div class="qr-area">
        <div class="qr-frame">
          <img v-if="qrImage" class="qr-image" :src="qrImage" alt="B 站扫码登录二维码" />
          <LoadingDots v-else />
        </div>
        <div class="status-line">
          <span class="status-dot" :class="statusClass" />
          <span>{{ statusText }}</span>
        </div>
      </div>

      <div v-if="auth.biliUser" class="user-line">
        <img v-if="auth.biliUser.face" :src="mediaUrl(auth.biliUser.face)" :alt="auth.biliUser.name" />
        <span>{{ auth.biliUser.name }}</span>
      </div>

      <p v-if="auth.biliError" class="error-text">{{ auth.biliError }}</p>

      <div class="actions">
        <button class="primary-btn" :disabled="auth.isQrLoading" @click="refreshQr">
          <AppIcon name="repeat" :size="16" />
          <span>刷新二维码</span>
        </button>
        <button v-if="auth.biliConnected" class="ghost-btn" @click="enterApp">
          <AppIcon name="play" :size="14" />
          <span>进入应用</span>
        </button>
        <button v-if="auth.biliConnected" class="text-btn" @click="logout">
          退出登录
        </button>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { useRoute, useRouter } from 'vue-router'
import { mediaUrl } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import AppIcon from '@/components/base/AppIcon.vue'
import LoadingDots from '@/components/base/LoadingDots.vue'
import iconUrl from '@/assets/icon.png'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const qrImage = ref('')

let pollTimer: ReturnType<typeof setInterval> | null = null

const redirectTarget = computed(() => {
  const redirect = route.query.redirect
  return typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/'
})

const statusText = computed(() => {
  if (auth.biliConnected) return '已登录'
  if (auth.isQrLoading) return '正在生成二维码'
  if (!auth.qrStatus) return '请使用哔哩哔哩客户端扫码'
  if (auth.qrStatus.status === 'scanned') return '已扫码，请在手机上确认'
  if (auth.qrStatus.status === 'expired') return '二维码已过期'
  if (auth.qrStatus.status === 'waiting') return '等待扫码'
  return auth.qrStatus.message || '等待确认'
})

const statusClass = computed(() => {
  if (auth.biliConnected) return 'ok'
  if (auth.qrStatus?.status === 'expired' || auth.biliError) return 'error'
  if (auth.qrStatus?.status === 'scanned') return 'pending'
  return ''
})

onMounted(async () => {
  await auth.initializeBili(true)
  if (!auth.biliConnected) {
    await refreshQr()
  }
})

onBeforeUnmount(() => {
  stopPolling()
})

async function refreshQr() {
  stopPolling()
  qrImage.value = ''
  const qr = await auth.startQrLogin()
  qrImage.value = await QRCode.toDataURL(qr.url, {
    width: 240,
    margin: 1,
    color: {
      dark: '#18181b',
      light: '#ffffff',
    },
  })
  pollTimer = setInterval(pollQr, qr.pollIntervalMs)
}

async function pollQr() {
  const result = await auth.pollQrLogin()
  if (!result) return
  if (result.status === 'confirmed') {
    stopPolling()
    enterApp()
  } else if (result.status === 'expired') {
    stopPolling()
  }
}

function enterApp() {
  router.replace(redirectTarget.value)
}

async function logout() {
  stopPolling()
  await auth.disconnectBili()
  await refreshQr()
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px 20px;
  background:
    linear-gradient(180deg, rgba(251, 114, 153, 0.12), transparent 42%),
    var(--color-bg-app);
}

.login-panel {
  width: min(420px, 100%);
  padding: 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-medium);
  background: var(--color-bg-content);
  box-shadow: var(--shadow-popup);
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-logo {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  object-fit: cover;
}

.brand h1 {
  font-size: 22px;
  line-height: 1.2;
  color: var(--color-text-primary);
}

.brand p {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.qr-area {
  margin-top: 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.qr-frame {
  width: 256px;
  height: 256px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: #fff;
}

.qr-image {
  width: 240px;
  height: 240px;
}

.status-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
}

.status-dot.pending {
  background: #febc2e;
}

.status-dot.ok {
  background: #28c840;
}

.status-dot.error {
  background: #ff5f57;
}

.user-line {
  margin-top: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-primary);
  font-weight: 500;
}

.user-line img {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
}

.error-text {
  margin-top: 14px;
  color: var(--color-primary);
  font-size: 13px;
  text-align: center;
}

.actions {
  margin-top: 24px;
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
}

.primary-btn,
.ghost-btn {
  height: 36px;
  padding: 0 16px;
  border-radius: var(--radius-small);
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.primary-btn {
  background: var(--color-primary);
  color: #fff;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.ghost-btn {
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  background: transparent;
}

.ghost-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.text-btn {
  height: 36px;
  padding: 0 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.text-btn:hover {
  color: var(--color-primary);
}
</style>
