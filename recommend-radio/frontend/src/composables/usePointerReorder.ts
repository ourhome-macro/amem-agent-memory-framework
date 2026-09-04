import { onUnmounted, ref } from 'vue'

interface PointerReorderOptions {
  dataAttribute: string
  onMove: (from: number, to: number) => void
}

export function usePointerReorder({ dataAttribute, onMove }: PointerReorderOptions) {
  const dragIndex = ref<number | null>(null)
  const dropIndex = ref<number | null>(null)

  let pointerId: number | null = null

  function startReorder(index: number, event: PointerEvent) {
    if (event.button !== 0) return
    event.preventDefault()
    pointerId = event.pointerId
    dragIndex.value = index
    dropIndex.value = index
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', cancelReorder)
  }

  function handlePointerMove(event: PointerEvent) {
    if (pointerId !== null && event.pointerId !== pointerId) return
    const target = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest(`[${dataAttribute}]`) as HTMLElement | null
    const nextIndex = Number(target?.getAttribute(dataAttribute))
    dropIndex.value = Number.isFinite(nextIndex) ? nextIndex : null
  }

  function handlePointerUp(event: PointerEvent) {
    if (pointerId !== null && event.pointerId !== pointerId) return
    const from = dragIndex.value
    const to = dropIndex.value
    if (from !== null && to !== null && from !== to) {
      onMove(from, to)
    }
    cancelReorder()
  }

  function cancelReorder() {
    pointerId = null
    dragIndex.value = null
    dropIndex.value = null
    window.removeEventListener('pointermove', handlePointerMove)
    window.removeEventListener('pointerup', handlePointerUp)
    window.removeEventListener('pointercancel', cancelReorder)
  }

  onUnmounted(cancelReorder)

  return {
    dragIndex,
    dropIndex,
    startReorder,
    cancelReorder,
  }
}
