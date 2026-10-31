// 与 app/tasks.py 的 meta 字典、app/api.py 的端点一一对应。
// 后端加字段时先改这里，vue-tsc 会在漏改处报错。

/** four 只出现在旧任务里，等价于 quality */
export type Mode = 'vocals' | 'fast' | 'quality' | 'four'

/** tasks._finish 写的是 cancelled（双 l），不要写成 canceled */
export type JobState =
  | 'queued'
  | 'decoding'
  | 'separating'
  | 'writing'
  | 'pitching'
  | 'ready'
  | 'failed'
  | 'cancelled'

export interface StemEntry {
  name: string
  file: string
  duration: number
  /** 派生轨：它来自哪条轨（如 vocals） */
  derived_from?: string
  /** 派生轨角色：lead = 人声主体，backing = 伴奏残留 */
  role?: string
}

export interface PitchState {
  state: 'queued' | 'running' | 'ready' | 'failed'
  fraction: number
  /** 只有 failed 时后端才写 */
  failure?: string
}

/** 提纯（人声主体 / 伴奏残留）的任务状态 */
export type RefineState = PitchState

/** GAME 推理参数，语义与 OpenUtau 的转录界面一致 */
export interface NotesParams {
  /** 导出用的曲速 */
  tempo: number
  /** 采样步数 */
  sampling_steps: number
  /** 边界阈值 */
  boundary_threshold: number
  /** 边界半径（帧） */
  boundary_radius: number
  /** 分数阈值 */
  score_threshold: number
  /** 并行大小：一批喂给模型的切片数 */
  batch_size: number
  /** 最大并行持续时间：一批内所有切片的总秒数上限，0 = 不限 */
  max_batch_duration: number
  /** 语言代码，null = 通用 */
  language: string | null
}

export interface NotesState extends PitchState {
  /** 用户提交时填的参数，导出时复用其中的曲速 */
  params?: NotesParams
}

export interface NotesPayload {
  version: number
  model: string
  stem: string
  hop_ms: number
  tempo: number
  params: NotesParams
  segments: { start_sec: number; dur_sec: number; midi: number; cents_offset: number }[]
  model_segments: { start_sec: number; dur_sec: number; midi: number; voiced: boolean }[]
}

export interface JobMeta {
  id: string
  mode: Mode
  filename: string
  state: JobState
  stage: string
  message: string
  fraction: number
  /** separating 阶段带 {stem, index, total}，其余为空对象 */
  extra: { stem?: string; index?: number; total?: number }
  failure: string | null
  created: number
  /** 进入终态（ready/failed/cancelled）的时刻；进行中的任务与老任务是空 */
  finished?: number | null
  stems: StemEntry[]
  pitch: Record<string, PitchState>
  /** 提纯任务状态，按轨名索引 */
  refine: Record<string, RefineState>
  /** 音符提取任务状态，按轨名索引 */
  notes: Record<string, NotesState>
  /** 设备实际用的推理后端，解码后才有值 */
  ep: string | null
  duration: number | null
}

export interface PitchCurve {
  hop_ms: number
  sample_rate: number
  times: number[]
  f0: number[]
  voiced: boolean[]
  confidence: number[]
}

export interface Peaks {
  min: number[]
  max: number[]
}

export interface EngineStatus {
  ep: string
  providers: string[]
  version: string
  fallback_reason: string | null
}

/** 后端 JOB_STATES 里「还在跑」的那几个 */
export const ACTIVE_STATES: readonly JobState[] = [
  'queued',
  'decoding',
  'separating',
  'writing',
  'pitching',
]

export function isActive(state: JobState): boolean {
  return ACTIVE_STATES.includes(state)
}

/** 任务本身或它身上的音高还在跑：决定要不要继续轮询 */
export function needsPolling(job: JobMeta): boolean {
  if (isActive(job.state)) return true
  return Object.values(job.pitch ?? {}).some(
    (entry) => entry.state === 'queued' || entry.state === 'running',
  )
}

export const STEM_LABELS: Record<string, string> = {
  vocals: '人声',
  drums: '鼓',
  bass: '贝斯',
  other: '其他',
  source: '原始音频',
}

/** 派生轨（提纯产物）没有固定文案，按角色拼：人声主体 / 伴奏残留 */
export function stemLabel(name: string): string {
  const direct = STEM_LABELS[name]
  if (direct) return direct
  const match = name.match(/^(.*)_(lead|backing)$/)
  if (match) {
    const base = STEM_LABELS[match[1]] ?? match[1]
    return match[2] === 'lead' ? `${base}主体` : `${base}残留`
  }
  return name
}

/** 人声永远置顶：提纯/音符/工程都挂在它身上 */
export const STEM_ORDER = ['vocals', 'drums', 'bass', 'other']

export function stemRank(name: string): number {
  const index = STEM_ORDER.indexOf(name)
  if (index >= 0) return index
  // 派生轨紧跟在它来源轨后面：人声主体 -> 伴奏残留
  const match = name.match(/^(.*)_(lead|backing)$/)
  if (match) {
    const base = STEM_ORDER.indexOf(match[1])
    if (base >= 0) return base + (match[2] === 'lead' ? 0.1 : 0.2)
  }
  return STEM_ORDER.length
}

export const MODE_LABELS: Record<Mode, string> = {
  vocals: '仅人声',
  fast: '快速四轨',
  quality: '质量四轨',
  four: '质量四轨',
}

export const STATE_LABELS: Record<string, string> = {
  queued: '排队中',
  decoding: '解码',
  separating: '分离',
  writing: '写入',
  pitching: '音高',
  ready: '就绪',
  failed: '失败',
  cancelled: '已取消',
}
