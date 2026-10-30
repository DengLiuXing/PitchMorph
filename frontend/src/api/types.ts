// 与 app/schemas.py 保持同步：后端加字段时先改这里，vue-tsc 会指出漏改的地方。

export type Stage =
  | 'queued'
  | 'decoding'
  | 'separating'
  | 'pitch'
  | 'notes'
  | 'done'
  | 'failed'
  | 'canceled'

/** separate = 只分离；analyze = 只分析音高与音符；both = 先分离再分析人声轨 */
export type Scope = 'separate' | 'analyze' | 'both'

/** 创建任务时给全的参数，按阶段分组；缺省即用后端默认值 */
export interface JobParams {
  separate?: { tier?: string }
  pitch?: {
    f0_min?: number
    f0_max?: number
    threshold?: number
    gap_ms?: number
    onsets?: boolean
  }
  notes?: {
    /** GAME 体积：small（默认，48 MB）/ medium（190 MB，精度略高）/ large（349 MB，未实测） */
    model?: string
    nsteps?: number
    seg_threshold?: number
    seg_radius?: number
    est_threshold?: number
    language?: string
  }
  /** 导出平滑：中值滤波窗口（帧，奇数）。创建时定稿，导出面板可临时覆盖 */
  exports?: { smooth?: number }
}

export interface Job {
  id: string
  filename: string
  tier: string
  scope: Scope
  /** 派生分析任务的父任务；它的音频来自父任务的 vocals 轨 */
  parent_id: string | null
  params: JobParams
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

/** worker 实际用的提取参数，随 pitch.json 一起回来 */
export interface PitchParams {
  f0_min: number
  f0_max: number
  threshold: number
  gap_ms: number
}

/** features/pitch.json 的形状。null = 无声，绝不是 0 Hz。 */
export interface PitchCurve {
  version: number
  model: string
  sample_rate: number
  hop_sec: number
  frames: number
  f0: (number | null)[]
  /** 老任务没有这个字段 */
  params?: PitchParams
}

/** 起音帧，与 PitchCurve.f0 同栅格（10 ms）。空数组 = 没启用或没检出。 */
export interface OnsetTrack {
  version: number
  sample_rate: number
  hop_sec: number
  frames: number
  /** 老任务或手工改坏时可能没有 */
  params?: {
    threshold: number
    min_gap_ms: number
    smooth_frames: number
  }
  onsets: number[]
}

export const ACTIVE_STAGES: readonly Stage[] = [
  'queued',
  'decoding',
  'separating',
  'pitch',
  'notes',
]

export const STEMS = ['drums', 'bass', 'other', 'vocals'] as const
export type StemName = (typeof STEMS)[number]

/** `input` 是「跳过分轨」任务里那条原始音频，不是 stem */
export const STEM_LABELS: Record<string, string> = {
  drums: '鼓',
  bass: '贝斯',
  other: '其他',
  vocals: '人声',
  input: '原始音频',
}

export function isActive(stage: Stage): boolean {
  return ACTIVE_STAGES.includes(stage)
}
