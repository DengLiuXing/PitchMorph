/** 00:12 / 00:12.4 —— 时间轴刻度与播放头读数共用一套，避免精度不一致 */
export function formatTime(seconds: number, withTenths = false): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  const whole = Math.floor(rest)
  const head = `${String(minutes).padStart(2, '0')}:${String(whole).padStart(2, '0')}`
  return withTenths ? `${head}.${Math.floor((rest - whole) * 10)}` : head
}

/** 处理耗时用的人读格式：12 秒 / 1 分 12 秒 / 1 时 05 分。别和时间轴 mm:ss 混用 */
export function formatSpan(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const total = Math.round(safe)
  if (total < 60) return `${Math.max(1, total)} 秒`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) {
    const rest = total - minutes * 60
    return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
  }
  return `${Math.floor(minutes / 60)} 时 ${String(minutes % 60).padStart(2, '0')} 分`
}

export function hzToMidi(hz: number): number {
  return 69 + 12 * Math.log2(hz / 440)
}

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

export function midiToName(midi: number): string {
  const rounded = Math.round(midi)
  return `${NOTE_NAMES[((rounded % 12) + 12) % 12]}${Math.floor(rounded / 12) - 1}`
}

export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function formatClockStamp(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
