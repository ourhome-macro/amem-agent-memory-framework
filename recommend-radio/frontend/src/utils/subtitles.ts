export function findActiveSubtitleLineIndex(
  lines: ReadonlyArray<{ from: number; to: number }>, currentTime: number,
): number {
  let low = 0
  let high = lines.length - 1
  let candidate = -1
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (lines[middle].from <= currentTime) {
      candidate = middle
      low = middle + 1
    } else high = middle - 1
  }
  return candidate >= 0 && currentTime < lines[candidate].to ? candidate : -1
}
