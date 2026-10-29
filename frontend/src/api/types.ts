// 与 app/schemas.py 保持同步：后端加字段时先改这里，vue-tsc 会指出漏改的地方。

export type Stage =
  | 'queued'
  | 'decoding'
  | 'separating'
  | 'pitch'
  | 'done'
  | 'failed'
  | 'canceled'

/** full = 分轨 + 音高；pitch = 上传的已经是干声，跳过分轨 */
export type Mode = 'full' | 'pitch'

export interface Job {
  id: string
  filename: string
  tier: string
  mode: Mode
  stage: Stage
  progress: number
  detail: string
  error: string | null
  duration_sec: number | null
  elapsed_sec: number
  created_at: number
  has_pitch: boolean
  has_stems: boolean
  has_exports: boolean
  /** 实际落盘的轨。「人声」档只有 vocals，不要写死四条 */
  sources: string[]
}

export interface Tier {
  id: string
  label: string
}

export interface ModelList {
  default: string
  tiers: Tier[]
}

export interface Health {
  ok: boolean
  ffmpeg: boolean
  max_upload_mb: number
  max_duration_sec: number
}

/** features/pitch.json 的形状。null = 无声，绝不是 0 Hz。 */
export interface PitchCurve {
  version: number
  model: string
  sample_rate: number
  hop_sec: number
  frames: number
  f0: (number | null)[]
}

export const ACTIVE_STAGES: readonly Stage[] = [
  'queued',
  'decoding',
  'separating',
  'pitch',
]

export const STEMS = ['drums', 'bass', 'other', 'vocals'] as const
export type StemName = (typeof STEMS)[number]

export const STEM_LABELS: Record<StemName, string> = {
  drums: '鼓',
  bass: '贝斯',
  other: '其他',
  vocals: '人声',
}

export function isActive(stage: Stage): boolean {
  return ACTIVE_STAGES.includes(stage)
}
