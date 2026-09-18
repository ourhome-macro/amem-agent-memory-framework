import { onScopeDispose, ref, type Ref } from 'vue'
import {
  apiUrl,
  fetchAgentDialogueSession,
  fetchAgentDialogueTaskStatus,
  fetchRecommendationDiscovery,
  refreshAgentDialogueRecommendationCard,
} from '@/api/client'
import type {
  AgentDialogueCard,
  AgentDialogueResult,
  AgentDialogueSession,
  AgentDialogueStreamEvent,
} from '@/types'

const TASK_POLL_MS = 3_000
const DISCOVERY_POLL_MS = 2_500

export function useDialogueTasks(options: {
  session: Ref<AgentDialogueSession | null>
  sending: Ref<boolean>
  errorMessage: Ref<string>
  applySession: (session: AgentDialogueSession | AgentDialogueResult) => void
  loadSessionHistory: () => Promise<void>
}) {
  const { session, sending, errorMessage, applySession, loadSessionHistory } = options
  const activeTaskId = ref('')
  const taskStage = ref('')
  const lastEventIds = new Map<string, string>()
  const completedTaskIds = new Set<string>()
  let eventSource: EventSource | null = null
  let connectedSessionId = ''
  let taskTimeout: number | null = null
  let taskPollTimer: number | null = null
  let discoveryPollTimer: number | null = null
  let discoveryPollInFlight = false

  function connectEventStream(sessionId?: string) {
    if (!sessionId) return
    // Closing an existing stream for the same session creates a gap in which
    // the gateway may advance its cursor past the final session event.
    if (connectedSessionId === sessionId && eventSource?.readyState !== EventSource.CLOSED) {
      scheduleDiscoveryReconciliation()
      return
    }
    closeEventStream()
    connectedSessionId = sessionId
    const params = new URLSearchParams({ sessionId })
    const lastEventId = lastEventIds.get(sessionId)
    if (lastEventId) params.set('lastEventId', lastEventId)
    const source = new EventSource(apiUrl(`/api/agent/events?${params.toString()}`), {
      withCredentials: true,
    })
    eventSource = source
    for (const eventType of ['task', 'progress', 'session', 'discovery', 'done', 'error']) {
      source.addEventListener(eventType, handleStreamEvent as EventListener)
    }
    source.onerror = () => {
      if (eventSource !== source) return
      taskStage.value = activeTaskId.value ? '连接恢复中' : ''
    }
    scheduleDiscoveryReconciliation()
  }

  function closeEventStream() {
    eventSource?.close()
    eventSource = null
    connectedSessionId = ''
    clearDiscoveryPoll()
    clearTaskPoll()
  }

  function handleStreamEvent(raw: Event) {
    const message = raw as MessageEvent<string>
    let event: AgentDialogueStreamEvent
    try {
      event = JSON.parse(message.data) as AgentDialogueStreamEvent
    } catch {
      return
    }
    if (!session.value || event.sessionId !== session.value.sessionId) return
    if (message.lastEventId) lastEventIds.set(event.sessionId, message.lastEventId)

    const nextSession = event.payload.session
    if (nextSession) {
      applySession(nextSession)
      scheduleDiscoveryReconciliation()
    }
    if (event.type === 'task' && event.status === 'queued' && sending.value && !activeTaskId.value) {
      activeTaskId.value = event.taskId
    }
    if (event.type === 'progress') {
      taskStage.value = streamStageLabel(event.payload.stage, event.payload.label)
    }
    if (event.type === 'error' && (!activeTaskId.value || event.taskId === activeTaskId.value)) {
      errorMessage.value = String(event.payload.message || '任务执行失败')
      finishActiveTask(event.taskId)
    }
    if (event.type === 'done') {
      completedTaskIds.add(event.taskId)
      finishActiveTask(event.taskId)
      scheduleDiscoveryReconciliation()
      void loadSessionHistory()
    }
  }

  function streamStageLabel(stage?: string, label?: string): string {
    if (label) return label
    return {
      routing: '正在理解你的需求',
      route: '已确定处理方式',
      memory: '正在读取相关记忆',
      recommendation: '正在整理候选歌曲',
      discovery: '正在补充新的候选',
    }[stage ?? ''] ?? '正在处理'
  }

  function armTaskTimeout(taskId: string) {
    clearTaskTimeout()
    taskTimeout = window.setTimeout(() => {
      if (activeTaskId.value !== taskId) return
      taskStage.value = '处理时间较长，正在继续等待结果'
      armTaskTimeout(taskId)
    }, 120_000)
  }

  function armTaskReconciliation(taskId: string, sessionId: string) {
    clearTaskPoll()
    const poll = async () => {
      if (activeTaskId.value !== taskId || session.value?.sessionId !== sessionId) return
      try {
        const task = await fetchAgentDialogueTaskStatus(taskId)
        if (task.status === 'completed') {
          const current = await fetchAgentDialogueSession(sessionId)
          if (activeTaskId.value !== taskId || session.value?.sessionId !== sessionId) return
          applySession(current)
          completedTaskIds.add(taskId)
          finishActiveTask(taskId)
          scheduleDiscoveryReconciliation()
          void loadSessionHistory()
          return
        }
        if (task.status === 'failed' || task.status === 'needs_reconciliation') {
          errorMessage.value = task.error || '任务执行失败'
          finishActiveTask(taskId)
          return
        }
      } catch {
        // SSE can still deliver the task. Try the durable status again.
      }
      if (activeTaskId.value === taskId) {
        taskPollTimer = window.setTimeout(poll, TASK_POLL_MS)
      }
    }
    taskPollTimer = window.setTimeout(poll, TASK_POLL_MS)
  }

  function pendingDiscoveryCards(): AgentDialogueCard[] {
    const current = session.value
    if (!current) return []
    const cards = new Map<string, AgentDialogueCard>()
    for (const card of [
      ...current.cards,
      ...current.messages.map((message) => message.card).filter((card): card is AgentDialogueCard => Boolean(card)),
    ]) {
      if (card.kind !== 'recommendation_carousel' || !card.discoveryJobId) continue
      if (card.discoveryStatus === 'completed' || card.discoveryStatus === 'failed') continue
      if ((card.recommendations?.length ?? 0) >= 8) continue
      cards.set(card.cardId, card)
    }
    return [...cards.values()]
  }

  function scheduleDiscoveryReconciliation() {
    clearDiscoveryPoll()
    if (!pendingDiscoveryCards().length) return
    discoveryPollTimer = window.setTimeout(reconcileDiscovery, DISCOVERY_POLL_MS)
  }

  async function reconcileDiscovery() {
    if (discoveryPollInFlight) return
    discoveryPollInFlight = true
    const sessionId = session.value?.sessionId
    try {
      for (const card of pendingDiscoveryCards()) {
        if (!sessionId || session.value?.sessionId !== sessionId) return
        try {
          const status = await fetchRecommendationDiscovery(card.discoveryJobId!)
          if (status.available && !['completed', 'failed', 'needs_reconciliation'].includes(status.status ?? '')) {
            continue
          }
          const refreshed = await refreshAgentDialogueRecommendationCard(card.cardId)
          if (session.value?.sessionId !== sessionId) return
          applySession(refreshed)
        } catch {
          // Preserve the card and retry while the page remains open.
        }
      }
    } finally {
      discoveryPollInFlight = false
      scheduleDiscoveryReconciliation()
    }
  }

  function clearTaskTimeout() {
    if (taskTimeout != null) window.clearTimeout(taskTimeout)
    taskTimeout = null
  }

  function clearTaskPoll() {
    if (taskPollTimer != null) window.clearTimeout(taskPollTimer)
    taskPollTimer = null
  }

  function clearDiscoveryPoll() {
    if (discoveryPollTimer != null) window.clearTimeout(discoveryPollTimer)
    discoveryPollTimer = null
  }

  function finishActiveTask(taskId: string) {
    if (activeTaskId.value && taskId !== activeTaskId.value) return
    clearTaskTimeout()
    clearTaskPoll()
    activeTaskId.value = ''
    taskStage.value = ''
    sending.value = false
  }

  onScopeDispose(() => {
    closeEventStream()
    clearTaskTimeout()
  })

  return {
    activeTaskId,
    taskStage,
    completedTaskIds,
    connectEventStream,
    closeEventStream,
    armTaskTimeout,
    armTaskReconciliation,
  }
}
