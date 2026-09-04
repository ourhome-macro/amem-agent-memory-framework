import { computed, readonly, ref, type Ref } from 'vue'
import { useAuthStore } from '@/stores/authStore'

const STORAGE_KEY_PREFIX = 'bilibili-radio.search-history.v1.user.'
const MAX_HISTORY_ITEMS = 10

const histories = new Map<string, Ref<string[]>>()
const requestedSearch = ref<{ id: number; keyword: string } | null>(null)
let requestId = 0

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.storageArea !== window.localStorage) return
    if (event.key === null) {
      histories.forEach((items) => {
        items.value = []
      })
      return
    }
    if (event.key.startsWith(STORAGE_KEY_PREFIX)) {
      const items = histories.get(event.key)
      if (items) items.value = parseHistory(event.newValue)
    }
  })
}

export function useSearchHistory() {
  const auth = useAuthStore()
  const storageKey = computed(() => {
    const userId = auth.appUser?.id?.trim()
    return userId ? `${STORAGE_KEY_PREFIX}${encodeURIComponent(userId)}` : null
  })
  const history = computed<readonly string[]>(() => {
    const key = storageKey.value
    return key ? getHistory(key).value : []
  })

  function rememberSuccessfulSearch(value: string): void {
    const keyword = value.trim()
    if (!keyword) return
    const key = storageKey.value
    if (!key) return
    const items = getHistory(key)
    items.value = [keyword, ...items.value.filter((item) => item !== keyword)].slice(
      0,
      MAX_HISTORY_ITEMS
    )
    persistHistory(key, items.value)
  }

  function removeHistoryItem(value: string): void {
    const keyword = value.trim()
    if (!keyword) return
    const key = storageKey.value
    if (!key) return
    const items = getHistory(key)
    items.value = items.value.filter((item) => item !== keyword)
    persistHistory(key, items.value)
  }

  function clearHistory(): void {
    const key = storageKey.value
    if (!key) return
    const items = getHistory(key)
    items.value = []
    persistHistory(key, items.value)
  }

  function requestSearch(value: string): void {
    const keyword = value.trim()
    if (!keyword) return
    requestedSearch.value = { id: ++requestId, keyword }
  }

  return {
    history: readonly(history),
    requestedSearch: readonly(requestedSearch),
    rememberSuccessfulSearch,
    removeHistoryItem,
    clearHistory,
    requestSearch,
  }
}

export function useSearchHistoryMenu(
  input: Ref<string>,
  menuId: string,
  onSelect: (value: string) => void
) {
  const {
    history: searchHistory,
    removeHistoryItem: removeStoredHistoryItem,
    clearHistory,
  } = useSearchHistory()
  const isOpen = ref(false)
  const activeIndex = ref(-1)
  const isVisible = computed(
    () => isOpen.value && !input.value.trim() && searchHistory.value.length > 0
  )
  const activeOptionId = computed(() =>
    isVisible.value && activeIndex.value >= 0
      ? `${menuId}-option-${activeIndex.value}`
      : undefined
  )

  function open(): void {
    isOpen.value = true
    activeIndex.value = -1
  }

  function close(): void {
    isOpen.value = false
    activeIndex.value = -1
  }

  function handleInput(): void {
    isOpen.value = !input.value.trim()
    activeIndex.value = -1
  }

  function handleFocusOut(event: FocusEvent): void {
    const root = event.currentTarget as HTMLElement
    const nextTarget = event.relatedTarget as Node | null
    if (!nextTarget || !root.contains(nextTarget)) close()
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      close()
      return
    }
    if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return
    if (!isVisible.value) {
      if (
        (event.key === 'ArrowDown' || event.key === 'ArrowUp') &&
        !input.value.trim() &&
        searchHistory.value.length
      ) {
        event.preventDefault()
        isOpen.value = true
        activeIndex.value = event.key === 'ArrowDown' ? 0 : searchHistory.value.length - 1
      }
      return
    }
    if (event.key === 'Enter' && activeIndex.value >= 0) {
      event.preventDefault()
      select(searchHistory.value[activeIndex.value])
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (activeIndex.value < 0) {
        activeIndex.value = event.key === 'ArrowDown' ? 0 : searchHistory.value.length - 1
        return
      }
      const offset = event.key === 'ArrowDown' ? 1 : -1
      activeIndex.value =
        (activeIndex.value + offset + searchHistory.value.length) % searchHistory.value.length
    }
  }

  function select(value: string): void {
    const keyword = value.trim()
    if (!keyword) return
    input.value = keyword
    close()
    onSelect(keyword)
  }

  function remove(value: string): void {
    removeStoredHistoryItem(value)
    activeIndex.value = Math.min(activeIndex.value, searchHistory.value.length - 1)
    if (!searchHistory.value.length) close()
  }

  function clear(): void {
    clearHistory()
    close()
  }

  return {
    searchHistory,
    activeIndex,
    isVisible,
    activeOptionId,
    open,
    close,
    handleInput,
    handleFocusOut,
    handleKeydown,
    select,
    remove,
    clear,
  }
}

function getHistory(storageKey: string): Ref<string[]> {
  const existing = histories.get(storageKey)
  if (existing) return existing
  const items = ref(readStoredHistory(storageKey))
  histories.set(storageKey, items)
  return items
}

function readStoredHistory(storageKey: string): string[] {
  if (typeof window === 'undefined') return []
  try {
    return parseHistory(window.localStorage.getItem(storageKey))
  } catch {
    return []
  }
}

function parseHistory(raw: string | null): string[] {
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const unique = new Set<string>()
    for (const item of parsed) {
      if (typeof item !== 'string') continue
      const keyword = item.trim()
      if (keyword) unique.add(keyword)
      if (unique.size >= MAX_HISTORY_ITEMS) break
    }
    return [...unique]
  } catch {
    return []
  }
}

function persistHistory(storageKey: string, items: readonly string[]): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(items))
  } catch {
    // Search remains usable when storage is unavailable or full.
  }
}
