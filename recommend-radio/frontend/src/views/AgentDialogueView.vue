<template>
  <div class="page agent-dialogue">
    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div v-if="loading && !session" class="loading-state">
      <span>正在读取对话状态</span>
    </div>

    <div v-else class="dialogue-shell">
      <button
        v-if="historyOpen"
        class="rail-backdrop"
        type="button"
        aria-label="关闭会话列表"
        @click="historyOpen = false"
      />
      <aside class="context-rail" :class="{ 'is-open': historyOpen }" aria-label="对话列表">
        <div class="rail-brand">
          <span class="rail-brand-icon"><AppIcon name="message" :size="18" /></span>
          <div>
            <small>RADIO AGENT</small>
            <strong>音乐搭子</strong>
          </div>
        </div>
        <button
          class="new-chat-btn"
          type="button"
          :disabled="loading || sending || undoing || !isAgentAvailable"
          @click="startNewSession"
        >
          <AppIcon name="plus" :size="16" />
          <span>新对话</span>
        </button>
        <label class="history-search">
          <AppIcon name="search" :size="15" />
          <input v-model="historyQuery" type="search" placeholder="搜索会话" aria-label="搜索会话" />
        </label>
        <div class="rail-section-label">
          <span>最近对话</span>
          <span>{{ sessionHistory.length }}</span>
        </div>
        <div class="context-list">
          <div
            v-for="item in filteredSessions"
            :key="item.sessionId"
            class="context-row"
          >
            <button
              class="context-item"
              :class="{ active: item.sessionId === session?.sessionId }"
              type="button"
              :disabled="loading || sending || deletingSessionId === item.sessionId"
              @click="selectSession(item.sessionId)"
            >
              <strong>{{ item.title }}</strong>
              <span>{{ item.preview }}</span>
              <small>{{ formatSessionTime(item.updatedAt) }}</small>
            </button>
            <button
              class="delete-session-btn"
              type="button"
              :disabled="loading || sending || deletingSessionId === item.sessionId"
              :aria-label="`删除对话：${item.title}`"
              :title="`删除对话：${item.title}`"
              @click.stop="deleteSession(item.sessionId)"
            >
              <AppIcon name="trash" :size="14" />
            </button>
          </div>
          <p v-if="!filteredSessions.length" class="empty-history">
            {{ historyQuery ? '没有找到匹配的对话' : '还没有历史对话' }}
          </p>
        </div>
        <div class="rail-account">
          <span class="rail-account-avatar"><AppIcon name="user" :size="16" /></span>
          <div class="rail-account-copy">
            <strong>{{ accountLabel }}</strong>
            <small>当前账户的独立空间</small>
          </div>
          <RouterLink to="/settings" title="API 设置" aria-label="API 设置">
            <AppIcon name="shield" :size="17" />
          </RouterLink>
        </div>
      </aside>

      <section class="chat-surface" :data-session-id="session?.sessionId">
        <header class="agent-header">
          <button
            class="mobile-history-btn"
            type="button"
            aria-label="打开会话列表"
            @click="historyOpen = true"
          >
            <AppIcon name="list" :size="18" />
          </button>
          <div class="agent-heading">
            <span class="agent-kicker">音乐助手 / 对话</span>
            <h1>{{ currentSessionTitle }}</h1>
            <p>{{ stateLabel }}</p>
          </div>
          <div class="agent-header-actions">
            <RouterLink class="key-status" to="/settings" title="查看当前账户的 API 设置">
              <span class="key-status-dot" :class="{ ready: isAgentAvailable }" />
              <span>{{ apiKeyLabel }}</span>
            </RouterLink>
            <button class="ghost-btn" type="button" :disabled="loading || undoing" title="刷新会话" @click="loadSession(session?.sessionId)">
              <AppIcon name="repeat" :size="16" />
              <span>刷新</span>
            </button>
          </div>
        </header>
        <div ref="messageListRef" class="message-list">
          <div v-if="apiKeyStatus && !isAgentAvailable" class="assistant-key-gate">
            <div class="empty-agent-mark"><AppIcon name="lock" :size="25" /></div>
            <h2>配置个人 API Key 后开始对话</h2>
            <p>音乐助手仅使用当前账户保存的 DeepSeek Key。未配置时，首页推荐使用规则模式。</p>
            <RouterLink to="/settings">前往 API 设置</RouterLink>
          </div>
          <div v-else-if="isFreshSession && !sending" class="empty-conversation">
            <div class="empty-agent-mark"><AppIcon name="message" :size="27" /></div>
            <span class="empty-eyebrow">YOUR MUSIC COMPANION</span>
            <h2>今天想听点什么？</h2>
            <p>聊聊心情、找一首合适的歌，或继续探索你的音乐偏好。</p>
            <div class="suggestion-list">
              <button type="button" @click="useSuggestedPrompt('给我推荐几首适合今晚听的歌')">
                <AppIcon name="disc" :size="16" />
                <span>给我推荐几首适合今晚听的歌</span>
                <AppIcon name="chevron" :size="14" />
              </button>
              <button type="button" @click="useSuggestedPrompt('根据我的喜好找一些新歌')">
                <AppIcon name="compass" :size="16" />
                <span>根据我的喜好找一些新歌</span>
                <AppIcon name="chevron" :size="14" />
              </button>
              <button type="button" @click="useSuggestedPrompt('聊聊我最近的听歌口味')">
                <AppIcon name="message" :size="16" />
                <span>聊聊我最近的听歌口味</span>
                <AppIcon name="chevron" :size="14" />
              </button>
            </div>
          </div>
          <article
            v-for="(message, messageIndex) in isFreshSession ? [] : messages"
            :key="message.id"
            class="message-row"
            :class="message.role"
          >
            <div class="message-avatar">
              <AppIcon :name="message.role === 'user' ? 'user' : 'message'" :size="16" />
            </div>
            <div class="message-cluster">
              <button
                v-if="isLastUserMessage(message, messageIndex)"
                class="message-undo-btn"
                type="button"
                :disabled="!canUndo"
                :title="undoing ? '撤回中' : '撤回这轮'"
                aria-label="撤回这轮"
                @click="undoLastMessage"
              >
                <AppIcon name="undo" :size="14" />
              </button>
              <div class="message-body">
                <span v-if="message.role === 'assistant'" class="speaker-label">音乐搭子</span>
                <div v-if="message.quotedContext" class="quote-line">
                  <span>原话</span>
                  <strong>{{ message.quotedContext.statement }}</strong>
                </div>
                <p>{{ message.content }}</p>
                <div
                  v-if="message.card"
                  class="inline-card"
                  :class="[message.card.kind, message.card.polarity]"
                  :data-card-id="message.card.cardId"
                  :data-discovery-status="message.card.discoveryStatus"
                >
                  <div class="inline-card-title">
                    <span>{{ cardKindLabel(message.card.kind) }}</span>
                    <strong>{{ message.card.title }}</strong>
                  </div>
                  <p v-if="message.card.prompt && message.card.prompt !== message.content">
                    {{ message.card.prompt }}
                  </p>
                  <blockquote v-if="shouldShowStatement(message.card)">
                    {{ message.card.statement }}
                  </blockquote>
                  <div v-if="message.card.kind === 'recommendation_carousel'" class="glass-song-list">
                    <article
                      v-for="item in message.card.recommendations ?? []"
                      :key="item.track.trackId ?? `${item.track.bvid}:${item.track.cid ?? item.reason}`"
                      class="song-glass-card"
                    >
                      <img :src="mediaUrl(item.track.cover)" :alt="item.track.title" loading="lazy" />
                      <div class="song-copy">
                        <strong :title="item.track.title">{{ item.track.title }}</strong>
                        <span>{{ item.track.owner }}</span>
                        <small>{{ item.reason }}</small>
                      </div>
                      <div class="song-actions">
                        <button class="song-action primary" type="button" title="播放" @click="playRecommendation(item)">
                          <AppIcon name="play" :size="15" />
                        </button>
                        <button
                          class="song-action muted"
                          type="button"
                          title="不感兴趣"
                          @click="dismissRecommendation(message.card, item)"
                        >
                          <AppIcon name="close" :size="15" />
                        </button>
                        <button class="song-action muted" type="button" title="引用并聊聊" @click="discussRecommendation(message.card, item)">
                          <AppIcon name="message" :size="15" />
                        </button>
                      </div>
                    </article>
                    <p v-if="!(message.card.recommendations?.length)" class="empty-text">
                      {{ message.card.discoveryJobId && message.card.discoveryStatus !== 'failed' ? '正在补充候选，完成后会自动回填到这张卡片…' : '这轮暂时没有拿到合适歌曲' }}
                    </p>
                  </div>
                  <div v-if="message.card.kind === 'memory_recall'" class="glass-song-list">
                    <article
                      v-for="track in message.card.tracks ?? []"
                      :key="track.trackId ?? `${track.bvid}:${track.cid ?? track.title}`"
                      class="song-glass-card"
                    >
                      <img :src="mediaUrl(track.cover)" :alt="track.title" loading="lazy" />
                      <div class="song-copy">
                        <strong :title="track.title">{{ track.title }}</strong>
                        <span>{{ track.owner }}</span>
                      </div>
                      <div class="song-actions">
                        <button class="song-action primary" type="button" title="播放" @click="playTrack(track)">
                          <AppIcon name="play" :size="15" />
                        </button>
                      </div>
                    </article>
                    <p v-if="!(message.card.tracks?.length)" class="empty-text">本地记录里暂时没找到</p>
                  </div>
                  <p v-if="message.card.error" class="card-error">{{ message.card.error }}</p>
                  <div v-if="message.card.actions.length" class="card-actions">
                    <button
                      v-for="action in message.card.actions"
                      :key="`${message.card.cardId}-${action}`"
                      type="button"
                      :disabled="message.card.status === 'confirming' || !isAgentAvailable"
                      @click="handleCardAction(message.card, action)"
                    >
                      <AppIcon :name="actionIcon(action)" :size="14" />
                      <span>{{ actionLabel(action) }}</span>
                    </button>
                  </div>
                </div>
                <time>{{ formatTime(message.createdAt) }}</time>
              </div>
            </div>
          </article>
          <article v-if="sending" class="message-row assistant thinking-row">
            <div class="message-avatar">
              <AppIcon name="message" :size="16" />
            </div>
            <div class="message-body thinking-body">
              <span>{{ thinkingLabel }}</span>
              <span class="typing-dots" aria-hidden="true">
                <i></i>
                <i></i>
                <i></i>
              </span>
            </div>
          </article>
        </div>

        <form class="composer" @submit.prevent="sendMessage">
          <div class="composer-shell">
            <div v-if="activeContext" class="context-bar">
              <span>{{ activeContext.statement }}</span>
              <button type="button" title="移除上下文" @click="activeContext = null">
                <AppIcon name="close" :size="14" />
              </button>
            </div>
            <div class="composer-row">
              <textarea
                ref="composerRef"
                v-model="messageText"
                maxlength="1000"
                rows="3"
                :placeholder="isAgentAvailable ? '向音乐搭子提问，或者说说你想听什么……' : '请先配置个人 API Key'"
                aria-label="输入消息"
                :disabled="!isAgentAvailable"
                @keydown.enter.exact.prevent="sendMessage"
              />
              <button class="send-btn" type="submit" :disabled="!canSend" title="发送消息">
                <AppIcon name="send" :size="18" />
                <span>发送</span>
              </button>
            </div>
            <div class="composer-footer">
              <span><AppIcon name="shield" :size="13" /> {{ apiKeyLabel }}</span>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useDialogueTasks } from '@/composables/useDialogueTasks'
import { computed, nextTick, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  createAgentDialogueSession,
  deleteAgentDialogueSession,
  fetchAgentDialogueSession,
  fetchAgentDialogueSessions,
  fetchDeepSeekKeyStatus,
  mediaUrl,
  recordRecommendationEvent,
  submitAgentDialogueTask,
  submitAgentDialogueCardFeedback,
  undoAgentDialogueMessage,
} from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { usePlayerStore } from '@/stores/playerStore'
import type {
  AgentDialogueCard,
  AgentDialogueCardAction,
  AgentDialogueCardKind,
  AgentDialogueCardStatus,
  AgentDialogueContext,
  AgentDialogueMessage,
  AgentDialogueResult,
  AgentDialogueSession,
  AgentDialogueSessionSummary,
  DeepSeekKeyStatus,
  RecommendationItem,
  Track,
} from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'

const player = usePlayerStore()
const auth = useAuthStore()
const session = ref<AgentDialogueSession | null>(null)
const loading = ref(false)
const historyLoading = ref(false)
const sending = ref(false)
const undoing = ref(false)
const errorMessage = ref('')
const messageText = ref('')
const composerRef = ref<HTMLTextAreaElement | null>(null)
const historyQuery = ref('')
const historyOpen = ref(false)
const deletingSessionId = ref('')
const apiKeyStatus = ref<DeepSeekKeyStatus | null>(null)
const pendingIntent = ref<'chat' | 'recommend' | 'control'>('chat')
const activeContext = ref<AgentDialogueContext | null>(null)
const messageListRef = ref<HTMLDivElement | null>(null)
const sessionHistory = ref<AgentDialogueSessionSummary[]>([])
const { activeTaskId, taskStage, completedTaskIds, connectEventStream, closeEventStream, armTaskTimeout, armTaskReconciliation } = useDialogueTasks({
  session, sending, errorMessage, applySession, loadSessionHistory,
})

onMounted(() => {
  void bootDialogue()
  void loadApiKeyStatus()
})


const messages = computed(() => session.value?.messages ?? [])
const isFreshSession = computed(() => (
  !messages.value.some((message) => message.role === 'user' || Boolean(message.card))
))
const accountLabel = computed(() => auth.appUser?.displayName?.trim() || '当前账户')
const filteredSessions = computed(() => {
  const query = historyQuery.value.trim().toLocaleLowerCase()
  if (!query) return sessionHistory.value
  return sessionHistory.value.filter((item) => (
    `${item.title} ${item.preview}`.toLocaleLowerCase().includes(query)
  ))
})
const currentSessionTitle = computed(() => (
  sessionHistory.value.find((item) => item.sessionId === session.value?.sessionId)?.title || '新对话'
))
const apiKeyLabel = computed(() => {
  const status = apiKeyStatus.value
  if (!status) return '配置状态未知'
  if (status.provider !== 'deepseek') return '当前模型未启用 DeepSeek'
  if (status.configured) return '个人 API Key'
  return '请配置个人 API Key'
})
const isAgentAvailable = computed(() => (
  apiKeyStatus.value?.provider === 'deepseek' && apiKeyStatus.value.configured
))
const lastUserMessageIndex = computed(() => {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    if (messages.value[index]?.role === 'user') return index
  }
  return -1
})
const canSend = computed(() => (
  Boolean(messageText.value.trim()) && isAgentAvailable.value
  && !loading.value && !sending.value && !undoing.value
))
const canUndo = computed(() => (
  Boolean(session.value?.messages.some((message) => message.role === 'user'))
  && isAgentAvailable.value
  && !loading.value
  && !sending.value
  && !undoing.value
))
const thinkingLabel = computed(() => {
  if (taskStage.value) return taskStage.value
  if (pendingIntent.value === 'recommend') return '正在找歌'
  if (pendingIntent.value === 'control') return '正在处理'
  return '正在思考'
})
const stateLabel = computed(() => {
  const focus = session.value?.focus
  return focus ? `当前上下文：${focus}` : '聊天、找歌、解释推荐都在这里'
})

async function bootDialogue() {
  await loadSession()
  await loadSessionHistory()
}

async function loadApiKeyStatus() {
  try {
    apiKeyStatus.value = await fetchDeepSeekKeyStatus()
  } catch {
    apiKeyStatus.value = null
  }
}

function useSuggestedPrompt(prompt: string) {
  messageText.value = prompt
  void nextTick(() => composerRef.value?.focus())
}

async function loadSession(sessionId?: string) {
  loading.value = true
  errorMessage.value = ''
  try {
    applySession(await fetchAgentDialogueSession(sessionId))
    connectEventStream(session.value?.sessionId)
    void loadSessionHistory()
  } catch (error) {
    errorMessage.value = errorToMessage(error, '对话状态读取失败')
  } finally {
    loading.value = false
  }
}

async function loadSessionHistory() {
  if (historyLoading.value) return
  historyLoading.value = true
  try {
    const result = await fetchAgentDialogueSessions()
    sessionHistory.value = result.items
  } catch {
    sessionHistory.value = []
  } finally {
    historyLoading.value = false
  }
}

async function startNewSession() {
  if (loading.value || sending.value || undoing.value || !isAgentAvailable.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    applySession(await createAgentDialogueSession())
    historyQuery.value = ''
    historyOpen.value = false
    connectEventStream(session.value?.sessionId)
    await loadSessionHistory()
  } catch (error) {
    errorMessage.value = errorToMessage(error, '新聊天创建失败')
  } finally {
    loading.value = false
  }
}

async function selectSession(sessionId: string) {
  if (
    !sessionId
    || sessionId === session.value?.sessionId
    || loading.value
    || sending.value
    || undoing.value
  ) return
  historyOpen.value = false
  await loadSession(sessionId)
}

async function deleteSession(sessionId: string) {
  if (!sessionId || loading.value || sending.value || deletingSessionId.value) return
  if (!window.confirm('确定删除这段对话？删除后无法恢复。')) return
  deletingSessionId.value = sessionId
  errorMessage.value = ''
  try {
    await deleteAgentDialogueSession(sessionId)
    const result = await fetchAgentDialogueSessions()
    sessionHistory.value = result.items
    if (session.value?.sessionId === sessionId) {
      activeContext.value = null
      closeEventStream()
      const nextSession = result.items[0]
      if (nextSession) {
        await loadSession(nextSession.sessionId)
      } else {
        session.value = null
      }
    }
  } catch (error) {
    errorMessage.value = errorToMessage(error, '删除对话失败')
  } finally {
    deletingSessionId.value = ''
  }
}

async function sendMessage() {
  const text = messageText.value.trim()
  if (!text || !isAgentAvailable.value || loading.value || sending.value || undoing.value) return

  const context = activeContext.value
  const optimisticMessage: AgentDialogueMessage = {
    id: `local:${Date.now()}`,
    role: 'user',
    content: text,
    cardId: context?.cardId,
    createdAt: new Date().toISOString(),
    quotedContext: context,
  }
  if (session.value) {
    session.value = {
      ...session.value,
      messages: [...session.value.messages, optimisticMessage],
    }
  }

  messageText.value = ''
  activeContext.value = null
  sending.value = true
  pendingIntent.value = classifyLocalIntent(text)
  errorMessage.value = ''
  applyLocalControl(text)
  await nextTick()
  scrollToBottom()

  let accepted = false
  try {
    const task = await submitAgentDialogueTask({
      message: text,
      sessionId: session.value?.sessionId,
      contextCardId: context?.cardId,
      contextTrackId: context?.trackId,
    })
    accepted = true
    connectEventStream(task.sessionId)
    if (completedTaskIds.has(task.taskId)) {
      completedTaskIds.delete(task.taskId)
      sending.value = false
    } else {
      activeTaskId.value = task.taskId
      taskStage.value = pendingIntent.value === 'recommend' ? '正在理解你的听歌需求' : '正在理解你的消息'
      armTaskTimeout(task.taskId)
      armTaskReconciliation(task.taskId, task.sessionId)
    }
  } catch (error) {
    errorMessage.value = errorToMessage(error, '消息发送失败')
  } finally {
    if (!accepted) sending.value = false
  }
}

function discussRecommendation(card: AgentDialogueCard, item: RecommendationItem) {
  if (!isAgentAvailable.value) return
  activeContext.value = {
    ...cardToContext(card),
    statement: `《${item.track.title}》${item.track.owner ? `（${item.track.owner}）` : ''}`,
    sourceText: item.track.title,
    topic: item.track.title,
    polarity: 'neutral',
    trackId: item.track.trackId,
  }
  messageText.value = `聊聊这首歌：`
}

async function undoLastMessage() {
  if (!session.value || !canUndo.value) return

  undoing.value = true
  errorMessage.value = ''
  activeContext.value = null
  try {
    const result = await undoAgentDialogueMessage(session.value.sessionId)
    applySession(result)
    if (!result.undone && result.message) {
      errorMessage.value = result.message
    }
    void loadSessionHistory()
  } catch (error) {
    errorMessage.value = errorToMessage(error, '撤回失败')
  } finally {
    undoing.value = false
  }
}

async function handleCardAction(card: AgentDialogueCard, action: AgentDialogueCardAction) {
  if (!session.value || !isAgentAvailable.value) return

  const previous = session.value
  const optimisticStatus = statusFromAction(action)
  setLocalCardStatus(card.cardId, optimisticStatus)
  errorMessage.value = ''

  if (action === 'discuss') {
    activeContext.value = cardToContext(card)
  }

  try {
    const result = await submitAgentDialogueCardFeedback(card.cardId, action)
    applySession(result)
    if (action === 'discuss') {
      activeContext.value = cardToContext(card)
    }
  } catch (error) {
    session.value = previous
    setLocalCardStatus(card.cardId, 'failed')
    errorMessage.value = errorToMessage(error, '卡片状态更新失败')
  }
}

function applySession(nextSession: AgentDialogueSession | AgentDialogueResult) {
  session.value = nextSession
  const card = [...nextSession.messages].reverse().find(
    (message) => message.card?.kind === 'recommendation_carousel'
  )?.card
  card?.recommendations?.slice(0, 2).forEach((item) => player.prewarmTrack(item.track))
  void nextTick(scrollToBottom)
}

function isLastUserMessage(message: AgentDialogueMessage, index: number): boolean {
  return message.role === 'user' && index === lastUserMessageIndex.value
}

function classifyLocalIntent(text: string): 'chat' | 'recommend' | 'control' {
  const normalized = normalizeCommandText(text)
  if (
    ['暂停', '停一下', '先停', '先暂停', '别放了', 'pause', '继续', '继续播放', '播放', '接着放', 'resume', '下一首', '下一个', '换下一首', 'next', '上一首', '上一个', 'previous', 'prev'].includes(normalized)
  ) {
    return 'control'
  }
  if (
    /换一批|再来一批|下一批|来一轮|给我推荐|推荐\d+|推荐几首|推几首|来几首|找几首|放几首|找点|来点|歌单|现在就想听|想听点/.test(text)
    && !/你觉得|为什么|为啥|原因|哪种歌手|什么歌手|哪个更适合|我的品味|我的口味/.test(text)
  ) {
    return 'recommend'
  }
  return 'chat'
}

function applyLocalControl(text: string): boolean {
  const normalized = normalizeCommandText(text)
  if (['暂停', '停一下', '先停', '先暂停', '别放了', 'pause'].includes(normalized)) {
    player.pause()
    return true
  }
  if (['继续', '继续播放', '播放', '接着放', 'resume'].includes(normalized)) {
    if (player.currentTrack) player.resume()
    return true
  }
  if (['下一首', '下一个', '换下一首', 'next'].includes(normalized)) {
    player.next()
    return true
  }
  if (['上一首', '上一个', 'previous', 'prev'].includes(normalized)) {
    player.prev()
    return true
  }
  return false
}

function normalizeCommandText(text: string): string {
  return text.trim().toLowerCase().replace(/[\s，,。.!！?？；;、~～]/g, '')
}

function setLocalCardStatus(cardId: string, status: AgentDialogueCardStatus) {
  if (!session.value) return
  session.value = {
    ...session.value,
    cards: session.value.cards.map((card) => (
      card.cardId === cardId ? { ...card, status, error: null } : card
    )),
  }
}

function setLocalCardRecommendations(cardId: string, recommendations: RecommendationItem[]) {
  if (!session.value) return
  session.value = {
    ...session.value,
    cards: session.value.cards.map((card) => (
      card.cardId === cardId ? { ...card, recommendations } : card
    )),
  }
}

function cardToContext(card: AgentDialogueCard): AgentDialogueContext {
  return {
    cardId: card.cardId,
    kind: card.kind,
    statement: card.statement,
    sourceText: card.sourceText || card.statement,
    topic: card.topic,
    polarity: card.polarity,
  }
}

function statusFromAction(action: AgentDialogueCardAction): AgentDialogueCardStatus {
  if (action === 'confirm' || action === 'accurate') return 'confirming'
  if (action === 'reject' || action === 'inaccurate') return 'rejected'
  if (action === 'later') return 'deferred'
  return 'discussing'
}

function actionLabel(action: AgentDialogueCardAction): string {
  return {
    confirm: '确认',
    reject: '拒绝',
    discuss: '聊聊',
    later: '稍后',
    accurate: '准',
    inaccurate: '不准',
  }[action]
}

function actionIcon(action: AgentDialogueCardAction): string {
  return {
    confirm: 'check',
    reject: 'close',
    discuss: 'message',
    later: 'clock',
    accurate: 'check',
    inaccurate: 'close',
  }[action]
}

function shouldShowStatement(card: AgentDialogueCard): boolean {
  return card.kind === 'pending_confirmation' && Boolean(card.statement)
}

function playRecommendation(item: RecommendationItem) {
  player.playTrack(item.track)
  void recordRecommendationEvent({
    trackId: item.track.trackId ?? trackIdentity(item.track),
    event: 'played',
    scene: 'conversation',
    source: item.source,
    reason: item.reason,
    score: item.score,
    recommendationTraceId: item.recommendationTraceId,
    sourceKeywordIds: item.sourceKeywordIds,
  })
}

function dismissRecommendation(card: AgentDialogueCard, item: RecommendationItem) {
  const trackId = item.track.trackId ?? trackIdentity(item.track)
  setLocalCardRecommendations(
    card.cardId,
    (card.recommendations ?? []).filter((candidate) => (
      (candidate.track.trackId ?? trackIdentity(candidate.track)) !== trackId
    ))
  )
  void recordRecommendationEvent({
    trackId,
    event: 'dismissed',
    scene: 'conversation',
    source: item.source,
    reason: item.reason,
    score: item.score,
    recommendationTraceId: item.recommendationTraceId,
    sourceKeywordIds: item.sourceKeywordIds,
  }).catch((error) => {
    errorMessage.value = errorToMessage(error, '反馈记录失败')
  })
}

function playTrack(track: Track) {
  player.playTrack(track)
}

function trackIdentity(track: Track): string {
  return `bili:${track.bvid}${track.cid != null ? `:cid:${track.cid}` : ''}`
}

function cardKindLabel(kind: AgentDialogueCardKind): string {
  return {
    interest_probe: '想问你',
    avoid_probe: '边界',
    pending_confirmation: '确认一下',
    recommendation_carousel: '给你几首',
    memory_recall: '听过的',
  }[kind]
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatSessionTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function scrollToBottom() {
  const target = messageListRef.value
  if (!target) return
  target.scrollTop = target.scrollHeight
}

function errorToMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
</script>

<style scoped>
.page {
  padding: 24px 32px;
}

.agent-dialogue {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.agent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.agent-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.agent-header h1 {
  font-size: 28px;
  line-height: 1.15;
  color: var(--color-text-primary);
}

.agent-header p {
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.ghost-btn {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  color: var(--color-text-secondary);
  background: var(--color-bg-content);
}

.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.dialogue-shell {
  min-height: 0;
  display: grid;
  grid-template-columns: 248px minmax(0, 880px);
  justify-content: center;
  align-items: stretch;
  gap: 18px;
}

.context-rail {
  min-width: 0;
}

.context-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.context-item {
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  text-align: left;
  color: var(--color-text-secondary);
  background: var(--color-bg-content);
}

.context-item.active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.context-item strong,
.context-item span,
.context-item small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-item strong {
  color: var(--color-text-primary);
  font-size: 13px;
}

.context-item span,
.context-item small {
  font-size: 12px;
}

.chat-surface {
  min-width: 0;
  min-height: min(680px, calc(100vh - 220px));
  display: grid;
  grid-template-rows: auto minmax(280px, 1fr) auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
  overflow: hidden;
}

.message-list {
  min-height: 0;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}

.message-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 10px;
}

.message-row.user {
  grid-template-columns: minmax(0, 1fr) 32px;
}

.message-row.user .message-avatar {
  order: 2;
}

.message-row.user .message-cluster {
  justify-self: end;
}

.message-row.user .message-body {
  background: var(--color-primary-soft);
}

.message-avatar {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.message-cluster {
  min-width: 0;
  display: flex;
  align-items: flex-end;
  gap: 6px;
}

.message-undo-btn {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  margin-bottom: 16px;
  border-radius: 50%;
  color: var(--color-text-tertiary);
  background: transparent;
}

.message-undo-btn:hover,
.message-undo-btn:focus-visible {
  color: var(--color-primary);
  background: var(--color-bg-hover);
}

.message-undo-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.message-body {
  max-width: min(680px, 100%);
  min-width: 0;
  padding: 10px 12px;
  border-radius: var(--radius-small);
  background: var(--color-bg-hover);
}

.message-body p {
  color: var(--color-text-primary);
  font-size: 14px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.message-body time {
  display: block;
  margin-top: 6px;
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.thinking-body {
  width: fit-content;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
}

.typing-dots {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.typing-dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: typing-bounce 1s infinite ease-in-out;
}

.typing-dots i:nth-child(2) {
  animation-delay: 0.14s;
}

.typing-dots i:nth-child(3) {
  animation-delay: 0.28s;
}

@keyframes typing-bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.45;
  }

  40% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

.quote-line,
.context-bar {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.quote-line {
  margin-bottom: 8px;
}

.quote-line span {
  flex: 0 0 auto;
  color: var(--color-primary);
  font-weight: 700;
}

.quote-line strong,
.context-bar span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.composer {
  border-top: 1px solid var(--color-border);
  padding: 12px;
  background: var(--color-bg-content);
}

.context-bar {
  min-height: 30px;
  margin-bottom: 10px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-hover);
}

.context-bar button {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--color-text-tertiary);
}

.context-bar button:hover {
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.composer-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 92px;
  gap: 10px;
  align-items: stretch;
}

.composer textarea {
  width: 100%;
  min-height: 72px;
  max-height: 160px;
  resize: vertical;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-app);
  color: var(--color-text-primary);
  font: inherit;
  line-height: 1.5;
}

.composer textarea:focus {
  outline: 2px solid var(--color-primary-soft);
  border-color: var(--color-primary);
  background: var(--color-bg-content);
}

.send-btn {
  min-height: 72px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: var(--radius-small);
  background: var(--color-primary);
  color: #fff;
  font-weight: 700;
}

.send-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.section-title {
  min-height: 26px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.section-title h2 {
  color: var(--color-text-primary);
  font-size: 17px;
  line-height: 1.3;
}

.section-title span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.inline-card {
  min-width: 0;
  padding: 13px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-content);
}

.inline-card {
  margin-top: 10px;
  background: rgba(255, 255, 255, 0.5);
}

.inline-card.positive {
  border-left: 3px solid #2f9e44;
}

.inline-card.negative {
  border-left: 3px solid #e03131;
}

.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.card-meta span {
  color: var(--color-primary);
  font-weight: 700;
}

.inline-card-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.inline-card-title span {
  flex: 0 0 auto;
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 700;
}

.inline-card-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
  font-size: 14px;
}

.inline-card p,
.inline-card blockquote,
.source-text {
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.inline-card blockquote {
  padding-left: 10px;
  border-left: 2px solid var(--color-border);
  color: var(--color-text-primary);
}

.source-text {
  padding: 8px;
  border-radius: var(--radius-small);
  background: var(--color-bg-hover);
}

.glass-song-list {
  max-height: 330px;
  margin-top: 12px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  border: 1px solid rgba(255, 255, 255, 0.38);
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.46);
  backdrop-filter: blur(16px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.34);
}

.song-glass-card {
  min-width: 0;
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 8px;
  border: 1px solid rgba(255, 255, 255, 0.42);
  border-radius: var(--radius-small);
  background: rgba(255, 255, 255, 0.58);
  box-shadow: 0 8px 24px rgba(24, 24, 27, 0.08);
}

.song-glass-card img {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-small);
  object-fit: cover;
  background: var(--color-bg-hover);
}

.song-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.song-copy strong,
.song-copy span,
.song-copy small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.song-copy strong {
  color: var(--color-text-primary);
  font-size: 13px;
}

.song-copy span,
.song-copy small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.song-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.song-action {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 50%;
}

.song-action.primary {
  color: #fff;
  background: var(--color-primary);
}

.song-action.muted {
  color: var(--color-text-tertiary);
  background: rgba(255, 255, 255, 0.68);
}

.song-action.muted:hover {
  color: #e03131;
  background: rgba(224, 49, 49, 0.12);
}

[data-theme="dark"] .glass-song-list {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(42, 42, 47, 0.52);
}

[data-theme="dark"] .song-glass-card {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(32, 32, 36, 0.72);
}

[data-theme="dark"] .inline-card {
  background: rgba(42, 42, 47, 0.52);
}

[data-theme="dark"] .song-action.muted {
  background: rgba(255, 255, 255, 0.08);
}

.card-error {
  color: #e03131;
}

.card-actions {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.card-actions button {
  min-height: 30px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  color: var(--color-text-secondary);
  background: var(--color-bg-content);
  font-size: 12px;
}

.card-actions button:hover:not(:disabled) {
  color: var(--color-primary);
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.card-actions button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.loading-state,
.empty-text,
.error-text {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.loading-state {
  min-height: 160px;
  display: grid;
  place-items: center;
}

.error-text,
.card-error {
  color: #e03131;
}

@media (max-width: 960px) {
  .dialogue-shell {
    grid-template-columns: 1fr;
  }

  .context-list {
    flex-direction: row;
    overflow-x: auto;
    padding-bottom: 4px;
  }

  .context-item {
    min-width: 220px;
  }
}

@media (max-width: 720px) {
  .page {
    padding: 20px;
  }

  .agent-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .composer-row {
    grid-template-columns: 1fr;
  }

  .send-btn {
    min-height: 40px;
  }
}

/* Conversation workspace */
.agent-dialogue {
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 0;
  display: block;
  background: var(--color-bg-content);
}

.dialogue-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: 242px minmax(0, 1fr);
  gap: 0;
  justify-content: stretch;
  background: var(--color-bg-content);
}

.context-rail {
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 22px 14px 16px;
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-sidebar);
}

.rail-brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 4px 9px 22px;
}

.rail-brand-icon,
.empty-agent-mark {
  display: grid;
  place-items: center;
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.rail-brand-icon {
  width: 34px;
  height: 34px;
  border-radius: 11px;
}

.rail-brand > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rail-brand small,
.empty-eyebrow,
.agent-kicker {
  color: var(--color-text-tertiary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.rail-brand strong {
  color: var(--color-text-primary);
  font-size: 15px;
}

.new-chat-btn {
  width: 100%;
  min-height: 40px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 13px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  color: var(--color-text-primary);
  background: var(--color-bg-content);
  font-size: 13px;
  font-weight: 650;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(24, 24, 27, 0.04);
}

.new-chat-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.new-chat-btn:disabled,
.context-item:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.history-search {
  height: 35px;
  margin-top: 18px;
  padding: 0 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--color-text-tertiary);
  background: var(--color-bg-hover);
}

.history-search:focus-within {
  border-color: var(--color-primary);
}

.history-search input {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: 0;
  color: var(--color-text-primary);
  background: transparent;
  font: inherit;
  font-size: 12px;
}

.history-search input::placeholder {
  color: var(--color-text-tertiary);
}

.rail-section-label {
  display: flex;
  justify-content: space-between;
  margin: 23px 8px 10px;
  color: var(--color-text-tertiary);
  font-size: 11px;
  font-weight: 650;
}

.context-list {
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 2px 4px;
}

.context-item {
  min-width: 0;
  padding: 11px 40px 11px 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  cursor: pointer;
}

.context-row {
  position: relative;
  min-width: 0;
}

.delete-session-btn {
  position: absolute;
  top: 9px;
  right: 7px;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  color: var(--color-text-tertiary);
  background: transparent;
  opacity: 0.55;
  cursor: pointer;
}

.context-row:hover .delete-session-btn,
.delete-session-btn:focus-visible {
  opacity: 1;
}

.delete-session-btn:hover:not(:disabled) {
  color: #d43f4c;
  background: rgba(212, 63, 76, 0.1);
}

.delete-session-btn:disabled {
  cursor: not-allowed;
  opacity: 0.3;
}

.context-item:hover:not(:disabled) {
  background: var(--color-bg-hover);
}

.context-item.active {
  border-color: var(--color-border);
  background: var(--color-bg-content);
  box-shadow: 0 1px 3px rgba(24, 24, 27, 0.04);
}

.context-item strong {
  font-size: 12px;
  font-weight: 650;
}

.context-item span {
  color: var(--color-text-secondary);
  font-size: 11px;
}

.context-item small {
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.empty-history {
  padding: 15px 10px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

.rail-account {
  min-height: 58px;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding: 12px 4px 0;
  border-top: 1px solid var(--color-border);
}

.rail-account-avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--color-text-secondary);
  background: var(--color-bg-hover);
}

.rail-account-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rail-account-copy strong,
.rail-account-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-account-copy strong {
  color: var(--color-text-primary);
  font-size: 12px;
}

.rail-account-copy small {
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.rail-account a {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: var(--color-text-secondary);
}

.rail-account a:hover {
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.chat-surface {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 0;
  border-radius: 0;
  background: var(--color-bg-content);
}

.agent-header {
  min-height: 76px;
  flex: 0 0 auto;
  flex-direction: row;
  align-items: center;
  padding: 12px 26px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-content);
}

.agent-heading {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.agent-header h1 {
  max-width: min(48vw, 480px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 17px;
  font-weight: 700;
}

.agent-header p {
  margin: 0;
  font-size: 11px;
}

.agent-header-actions {
  margin-left: auto;
}

.key-status {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  background: var(--color-bg-app);
  font-size: 11px;
  text-decoration: none;
}

.key-status:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.key-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
}

.key-status-dot.ready {
  background: #2da36b;
}

.ghost-btn {
  height: 32px;
  border-radius: 8px;
  font-size: 11px;
  cursor: pointer;
}

.mobile-history-btn,
.rail-backdrop {
  display: none;
}

.message-list {
  flex: 1;
  min-height: 0;
  gap: 23px;
  padding: 30px max(22px, calc((100% - 860px) / 2));
  scroll-behavior: smooth;
}

.message-row {
  width: 100%;
  max-width: 860px;
  align-self: center;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 13px;
}

.message-row.user {
  grid-template-columns: minmax(0, 1fr) 36px;
}

.message-avatar {
  width: 34px;
  height: 34px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg-app);
}

.message-row.user .message-avatar {
  color: var(--color-text-secondary);
  border-radius: 50%;
}

.message-cluster {
  align-items: flex-start;
}

.message-body {
  width: fit-content;
  max-width: min(720px, 100%);
  padding: 0;
  border-radius: 0;
  background: transparent;
}

.message-row.user .message-body {
  padding: 11px 15px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-bg-app);
}

.speaker-label {
  display: block;
  margin: 0 0 6px;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 700;
}

.message-body p {
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.message-body time {
  margin-top: 9px;
  font-size: 10px;
}

.message-undo-btn {
  margin: 6px 0 0;
}

.thinking-body {
  padding: 8px 0;
  background: transparent;
  font-size: 13px;
}

.empty-conversation {
  width: min(100%, 570px);
  min-height: 400px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  align-self: center;
  margin: auto;
  padding: 10px 0 42px;
}

.assistant-key-gate {
  width: min(100%, 570px);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  align-self: center;
  margin: auto;
  padding: 30px 0;
}

.assistant-key-gate h2 {
  color: var(--color-text-primary);
  font-size: 24px;
}

.assistant-key-gate p {
  margin: 10px 0 22px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.assistant-key-gate a {
  padding: 10px 15px;
  border-radius: 9px;
  color: white;
  background: var(--color-primary);
  font-size: 13px;
  font-weight: 650;
  text-decoration: none;
}

.empty-agent-mark {
  width: 54px;
  height: 54px;
  margin-bottom: 22px;
  border-radius: 17px;
}

.empty-conversation h2 {
  margin-top: 9px;
  color: var(--color-text-primary);
  font-size: clamp(24px, 3vw, 32px);
  line-height: 1.3;
}

.empty-conversation > p {
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.suggestion-list {
  width: 100%;
  display: grid;
  gap: 9px;
  margin-top: 30px;
}

.suggestion-list button {
  min-height: 47px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  color: var(--color-text-secondary);
  background: var(--color-bg-content);
  text-align: left;
  cursor: pointer;
}

.suggestion-list button:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.suggestion-list button span {
  flex: 1;
  color: var(--color-text-primary);
  font-size: 12px;
}

.suggestion-list button .app-icon:last-child {
  transform: rotate(-90deg);
}

.composer {
  flex: 0 0 auto;
  padding: 10px 22px 18px;
  border-top: 0;
  background: var(--color-bg-content);
}

.composer-shell {
  width: min(100%, 860px);
  margin: 0 auto;
  padding: 11px 12px 9px;
  border: 1px solid var(--color-border);
  border-radius: 16px;
  background: var(--color-bg-content);
  box-shadow: 0 8px 28px rgba(24, 24, 27, 0.07);
}

.composer-shell:focus-within {
  border-color: var(--color-primary);
}

.composer-row {
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}

.composer textarea {
  min-height: 64px;
  max-height: 170px;
  padding: 6px 9px;
  border: 0;
  outline: 0;
  resize: vertical;
  background: transparent;
  font-size: 13px;
}

.composer textarea:focus {
  border: 0;
  outline: 0;
  background: transparent;
}

.send-btn {
  min-width: 78px;
  min-height: 38px;
  padding: 0 13px;
  border-radius: 9px;
  font-size: 12px;
  cursor: pointer;
}

.composer-footer {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 8px 0;
  color: var(--color-text-tertiary);
  font-size: 10px;
}

.composer-footer span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.inline-card {
  padding: 15px;
  border-radius: 11px;
  background: var(--color-bg-content);
}

.glass-song-list {
  max-height: 360px;
  border-color: var(--color-border);
  background: var(--color-bg-app);
  backdrop-filter: none;
  box-shadow: none;
}

.song-glass-card {
  border-color: var(--color-border);
  background: var(--color-bg-content);
  box-shadow: 0 2px 9px rgba(24, 24, 27, 0.04);
}

.song-action.muted {
  background: var(--color-bg-hover);
}

.error-text {
  position: absolute;
  z-index: 5;
  top: 81px;
  left: 50%;
  width: max-content;
  max-width: min(90%, 620px);
  transform: translateX(-50%);
  padding: 9px 14px;
  border: 1px solid rgba(224, 49, 49, 0.24);
  border-radius: 9px;
  background: var(--color-bg-content);
  box-shadow: var(--shadow-popup);
}

@media (max-width: 1280px) {
  .dialogue-shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .context-rail {
    position: absolute;
    z-index: 12;
    inset: 0 auto 0 0;
    width: min(280px, 85vw);
    transform: translateX(-100%);
    transition: transform 180ms ease;
    box-shadow: var(--shadow-popup);
  }

  .context-rail.is-open {
    transform: translateX(0);
  }

  .rail-backdrop {
    position: absolute;
    z-index: 11;
    inset: 0;
    display: block;
    width: 100%;
    border: 0;
    background: rgba(12, 12, 16, 0.34);
  }

  .mobile-history-btn {
    width: 34px;
    height: 34px;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    border: 1px solid var(--color-border);
    border-radius: 9px;
    color: var(--color-text-secondary);
    background: var(--color-bg-content);
  }
}

@media (max-width: 720px) {
  .agent-header {
    min-height: 66px;
    flex-direction: row;
    align-items: center;
    gap: 9px;
    padding: 9px 12px;
  }

  .agent-header h1 {
    max-width: 38vw;
    font-size: 14px;
  }

  .agent-header p,
  .agent-kicker,
  .key-status span:last-child,
  .ghost-btn span,
  .composer-footer span:last-child {
    display: none;
  }

  .key-status {
    min-width: 32px;
    justify-content: center;
    padding: 0;
  }

  .ghost-btn {
    width: 32px;
    justify-content: center;
    padding: 0;
  }

  .message-list {
    padding: 20px 12px;
  }

  .message-row,
  .message-row.user {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 8px;
  }

  .message-row.user {
    grid-template-columns: minmax(0, 1fr) 28px;
  }

  .message-avatar {
    width: 28px;
    height: 28px;
  }

  .empty-conversation {
    min-height: 350px;
    padding: 0 9px;
  }

  .suggestion-list button {
    min-height: 44px;
  }

  .composer {
    padding: 8px 10px 12px;
  }

  .composer-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .composer textarea {
    min-height: 62px;
  }

  .send-btn {
    min-width: 38px;
    min-height: 36px;
    padding: 0;
  }

  .send-btn span {
    display: none;
  }

  .song-glass-card {
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .song-glass-card img {
    width: 42px;
    height: 42px;
  }

  .song-actions {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
