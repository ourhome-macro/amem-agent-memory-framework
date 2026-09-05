<template>
  <div class="page agent-dialogue">
    <header class="agent-header">
      <div>
        <h1>音乐搭子</h1>
        <p>{{ stateLabel }}</p>
      </div>
      <div class="agent-header-actions">
        <button class="ghost-btn" :disabled="loading || sending || undoing" title="新聊天" @click="startNewSession">
          <AppIcon name="plus" :size="16" />
          <span>新聊天</span>
        </button>
        <button class="ghost-btn" :disabled="loading || undoing" title="刷新" @click="loadSession(session?.sessionId)">
          <AppIcon name="repeat" :size="16" />
          <span>{{ loading ? '刷新中' : '刷新' }}</span>
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div v-if="loading && !session" class="loading-state">
      <span>正在读取对话状态</span>
    </div>

    <div v-else class="dialogue-shell">
      <aside class="context-rail">
        <div class="section-title">
          <h2>会话</h2>
        </div>
        <div class="context-list">
          <button
            v-for="item in sessionHistory"
            :key="item.sessionId"
            class="context-item"
            :class="{ active: item.sessionId === session?.sessionId }"
            type="button"
            :disabled="loading || sending"
            @click="selectSession(item.sessionId)"
          >
            <strong>{{ item.title }}</strong>
            <span>{{ item.preview }}</span>
            <small>{{ formatSessionTime(item.updatedAt) }}</small>
          </button>
        </div>
      </aside>

      <section class="chat-surface">
        <div v-if="topPositive.length || topNegative.length" class="chat-context-strip">
          <span>近期听感</span>
          <div class="signal-row">
            <span v-for="item in topPositive" :key="`pos-${item.name}`" class="signal-chip positive">
              {{ item.name }}
            </span>
            <span v-for="item in topNegative" :key="`neg-${item.name}`" class="signal-chip negative">
              少推 {{ item.name }}
            </span>
          </div>
        </div>
        <div ref="messageListRef" class="message-list">
          <article
            v-for="(message, messageIndex) in messages"
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
                <div v-if="message.quotedContext" class="quote-line">
                  <span>原话</span>
                  <strong>{{ message.quotedContext.statement }}</strong>
                </div>
                <p>{{ message.content }}</p>
                <div
                  v-if="message.card"
                  class="inline-card"
                  :class="[message.card.kind, message.card.polarity]"
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
                      :disabled="message.card.status === 'confirming'"
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
          <div v-if="activeContext" class="context-bar">
            <span>{{ activeContext.statement }}</span>
            <button type="button" title="移除上下文" @click="activeContext = null">
              <AppIcon name="close" :size="14" />
            </button>
          </div>
          <div class="composer-row">
            <textarea
              v-model="messageText"
              maxlength="1000"
              rows="3"
              placeholder="说说今天的状态、想听的歌，或者问我为什么这么推荐"
              @keydown.enter.exact.prevent="sendMessage"
            />
            <button class="send-btn" type="submit" :disabled="!canSend">
              <AppIcon name="send" :size="17" />
              <span>发送</span>
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  createAgentDialogueSession,
  fetchAgentDialogueSession,
  fetchAgentDialogueSessions,
  mediaUrl,
  recordRecommendationEvent,
  refreshAgentDialogueRecommendationCard,
  sendAgentDialogueMessage,
  submitAgentDialogueCardFeedback,
  undoAgentDialogueMessage,
} from '@/api/client'
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
  RecommendationItem,
  Track,
} from '@/types'
import AppIcon from '@/components/base/AppIcon.vue'

const player = usePlayerStore()
const session = ref<AgentDialogueSession | null>(null)
const loading = ref(false)
const historyLoading = ref(false)
const sending = ref(false)
const undoing = ref(false)
const errorMessage = ref('')
const messageText = ref('')
const pendingIntent = ref<'chat' | 'recommend' | 'control'>('chat')
const activeContext = ref<AgentDialogueContext | null>(null)
const messageListRef = ref<HTMLDivElement | null>(null)
const sessionHistory = ref<AgentDialogueSessionSummary[]>([])
const refreshingCards = ref(false)

onMounted(() => {
  void bootDialogue()
})

const messages = computed(() => session.value?.messages ?? [])
const lastUserMessageIndex = computed(() => {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    if (messages.value[index]?.role === 'user') return index
  }
  return -1
})
const topPositive = computed(() => session.value?.analysis.summary.topPositiveTopics ?? [])
const topNegative = computed(() => session.value?.analysis.summary.topNegativeTopics ?? [])
const canSend = computed(() => Boolean(messageText.value.trim()) && !sending.value && !undoing.value)
const canUndo = computed(() => (
  Boolean(session.value?.messages.some((message) => message.role === 'user'))
  && !loading.value
  && !sending.value
  && !undoing.value
))
const thinkingLabel = computed(() => {
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

async function loadSession(sessionId?: string) {
  loading.value = true
  errorMessage.value = ''
  try {
    applySession(await fetchAgentDialogueSession(sessionId))
    void refreshPendingRecommendationCards()
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
  if (loading.value || sending.value || undoing.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    applySession(await createAgentDialogueSession())
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
  await loadSession(sessionId)
}

async function sendMessage() {
  const text = messageText.value.trim()
  if (!text || sending.value || undoing.value) return

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

  try {
    applySession(await sendAgentDialogueMessage({
      message: text,
      sessionId: session.value?.sessionId,
      contextCardId: context?.cardId,
      contextTrackId: context?.trackId,
    }))
    void refreshPendingRecommendationCards()
    void loadSessionHistory()
  } catch (error) {
    errorMessage.value = errorToMessage(error, '消息发送失败')
  } finally {
    sending.value = false
  }
}

function discussRecommendation(card: AgentDialogueCard, item: RecommendationItem) {
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

async function refreshPendingRecommendationCards() {
  if (refreshingCards.value) return
  refreshingCards.value = true
  try {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const pending = session.value?.cards.find(card => (
        card.kind === 'recommendation_carousel'
        && card.discoveryJobId
        && !['completed', 'failed'].includes(card.discoveryStatus ?? '')
        && (card.recommendations?.length ?? 0) < 8
      ))
      if (!pending) return
      await new Promise(resolve => window.setTimeout(resolve, 1000))
      try {
        applySession(await refreshAgentDialogueRecommendationCard(pending.cardId))
      } catch {
        return
      }
    }
  } finally {
    refreshingCards.value = false
  }
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
  if (!session.value) return

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

.chat-context-strip {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  font-size: 12px;
  background: var(--color-bg-app);
}

.chat-context-strip > span {
  flex: 0 0 auto;
  font-weight: 700;
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

.signal-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.signal-chip {
  max-width: 100%;
  min-height: 26px;
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: var(--radius-small);
  font-size: 12px;
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
  overflow-wrap: anywhere;
}

.signal-chip.positive {
  color: #116329;
  background: #e8f5eb;
}

.signal-chip.negative {
  color: #8a241f;
  background: #fdeceb;
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
</style>
