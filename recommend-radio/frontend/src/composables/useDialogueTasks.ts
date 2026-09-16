import { ref, onScopeDispose, type Ref } from 'vue'
import { apiUrl } from '@/api/client'
import type { AgentDialogueSession, AgentDialogueResult, AgentDialogueStreamEvent } from '@/types'

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
let eventSource: EventSource | null = null
let taskTimeout: number | null = null
const lastEventIds = new Map<string, string>()
const completedTaskIds = new Set<string>()

function connectEventStream(sessionId?: string) {
  if (!sessionId) return
  closeEventStream()
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
    // Native EventSource reconnects and sends Last-Event-ID automatically.
    taskStage.value = activeTaskId.value ? '连接恢复中' : ''
  }
}

function closeEventStream() {
  eventSource?.close()
  eventSource = null
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
  if (nextSession) applySession(nextSession)
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
    errorMessage.value = '任务响应超时，请稍后刷新会话查看结果'
    finishActiveTask(taskId)
  }, 120_000)
}

function clearTaskTimeout() {
  if (taskTimeout != null) window.clearTimeout(taskTimeout)
  taskTimeout = null
}

function finishActiveTask(taskId: string) {
  if (activeTaskId.value && taskId !== activeTaskId.value) return
  clearTaskTimeout()
  activeTaskId.value = ''
  taskStage.value = ''
  sending.value = false
}


onScopeDispose(() => { closeEventStream(); clearTaskTimeout() })
return { activeTaskId, taskStage, completedTaskIds, connectEventStream, armTaskTimeout }
}
