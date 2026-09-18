<template>
  <main class="settings-page">
    <header>
      <h1>API 设置</h1>
      <p>为当前应用账户配置 DeepSeek API Key。</p>
    </header>

    <section class="settings-card" aria-labelledby="deepseek-heading">
      <div class="card-heading">
        <div>
          <h2 id="deepseek-heading">DeepSeek</h2>
          <p v-if="loading">正在读取配置…</p>
          <p v-else-if="status?.provider !== 'deepseek'">当前模型服务使用 {{ status?.provider }}；保存的 DeepSeek 密钥会在切换到 DeepSeek 后使用。</p>
          <p v-else-if="status?.configured">当前账户的密钥已生效。</p>
          <p v-else>尚未配置个人密钥。音乐助手不可用，首页推荐使用规则模式。</p>
        </div>
        <span v-if="!loading" class="status-badge">{{ status?.configured ? '已配置' : '未配置个人密钥' }}</span>
      </div>

      <form @submit.prevent="save">
        <label for="deepseek-key">API Key</label>
        <input
          id="deepseek-key"
          v-model="keyInput"
          type="password"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          maxlength="512"
          placeholder="输入新的 DeepSeek API Key"
          :disabled="saving || loading"
        />
        <p class="hint">保存后密钥仅存于服务端，页面不会再次显示。更新密钥直接输入新的值。</p>
        <p v-if="message" class="feedback" role="status">{{ message }}</p>
        <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
        <div class="actions">
          <button type="submit" class="primary" :disabled="saving || loading || !keyInput.trim()">
            {{ saving ? '保存中…' : '保存密钥' }}
          </button>
          <button
            v-if="status?.configured"
            type="button"
            class="secondary"
            :disabled="saving || loading"
            @click="remove"
          >
            删除个人密钥
          </button>
        </div>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchDeepSeekKeyStatus, removeDeepSeekKey, saveDeepSeekKey } from '@/api/client'
import type { DeepSeekKeyStatus } from '@/types'

const status = ref<DeepSeekKeyStatus | null>(null)
const keyInput = ref('')
const loading = ref(true)
const saving = ref(false)
const message = ref('')
const error = ref('')

onMounted(async () => {
  try {
    status.value = await fetchDeepSeekKeyStatus()
  } catch {
    error.value = '读取配置失败，请刷新页面重试。'
  } finally {
    loading.value = false
  }
})

async function save() {
  if (!keyInput.value.trim() || saving.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    await saveDeepSeekKey(keyInput.value)
    keyInput.value = ''
    status.value = await fetchDeepSeekKeyStatus()
    window.dispatchEvent(new Event('deepseek-key-updated'))
    message.value = status.value.provider === 'deepseek'
      ? '密钥已保存并生效。'
      : '密钥已保存，切换模型服务到 DeepSeek 后生效。'
  } catch {
    error.value = '保存失败。请检查密钥格式和服务器配置。'
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    await removeDeepSeekKey()
    keyInput.value = ''
    status.value = await fetchDeepSeekKeyStatus()
    window.dispatchEvent(new Event('deepseek-key-updated'))
    message.value = '个人密钥已删除；音乐助手已停用，推荐使用规则模式。'
  } catch {
    error.value = '删除失败，请重试。'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.settings-page { max-width: 760px; margin: 0 auto; padding: 36px 24px; color: var(--color-text-primary); }
h1 { font-size: 28px; margin: 0 0 8px; }
h2 { font-size: 20px; margin: 0 0 8px; }
p { color: var(--color-text-secondary); margin: 0; line-height: 1.6; }
.settings-card { margin-top: 28px; padding: 28px; border: 1px solid var(--color-border); border-radius: var(--radius-large); background: var(--color-bg-content); }
.card-heading { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 28px; }
.status-badge { white-space: nowrap; font-size: 12px; color: var(--color-primary); background: var(--color-primary-soft); border-radius: 999px; padding: 6px 10px; }
label { display: block; margin-bottom: 8px; font-weight: 600; }
input { box-sizing: border-box; width: 100%; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--radius-small); background: var(--color-bg-app); color: var(--color-text-primary); font: inherit; }
input:focus { outline: 2px solid var(--color-primary); outline-offset: 1px; }
.hint { margin-top: 10px; font-size: 13px; }
.feedback { margin-top: 14px; color: var(--color-primary); }
.feedback.error { color: #d43f4c; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
button { border-radius: var(--radius-small); padding: 10px 16px; font: inherit; cursor: pointer; }
button:disabled { opacity: .55; cursor: not-allowed; }
.primary { border: 1px solid var(--color-primary); color: white; background: var(--color-primary); }
.secondary { border: 1px solid var(--color-border); color: var(--color-text-primary); background: transparent; }
@media (max-width: 600px) { .settings-page { padding: 24px 16px; } .settings-card { padding: 20px; } .card-heading { display: block; } .status-badge { display: inline-block; margin-top: 12px; } }
</style>
