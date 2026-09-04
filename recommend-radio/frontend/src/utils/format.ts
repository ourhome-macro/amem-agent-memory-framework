/** 秒 → mm:ss（超过一小时显示 h:mm:ss） */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const pad = (n: number) => n.toString().padStart(2, '0')
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`
  return `${pad(m)}:${pad(s)}`
}

/** 播放量等大数字 → 1.2万 / 3.4亿 */
export function formatCount(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return `${n}`
}
