export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.0'
  const whole = Math.floor(seconds)
  const minutes = Math.floor(whole / 60)
  const rest = seconds - minutes * 60
  const padded = rest.toFixed(1).padStart(4, '0')
  return `${minutes}:${padded}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 2 ** 20).toFixed(1)} MB`
}

/** 音名（科学家记法），用于音高窗口的刻度 */
export function hzToNote(hz: number): string {
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  const midi = Math.round(69 + 12 * Math.log2(hz / 440))
  return `${names[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`
}

export const STAGE_TEXT: Record<string, string> = {
  queued: '排队中',
  decoding: '解码中',
  separating: '分轨中',
  pitch: '提取音高',
  notes: '音符边界',
  done: '已完成',
  failed: '失败',
  canceled: '已取消',
}
