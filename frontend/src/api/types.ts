// Shapes mirrored from app/schemas.py. Keep them in sync when the API changes.

export type JobStage =
  | 'queued'
  | 'downloading'
  | 'loading'
  | 'separating'
  | 'done'
  | 'canceled'
  | 'error'

export interface Job {
  id: string
  stage: JobStage
  progress: number
  stem: string | null
  done: number
  total: number
  parallel: number
  elapsed_sec: number
  wait_sec: number
  duration_sec: number
  stems: Record<string, string>
  error: string | null
  filename: string
  size_bytes: number
  tier: string
  cancel_requested: boolean
  created_at: number
}

export interface ModelTier {
  key: string
  label: string
  model: string
  stems: string[]
  download_mb: number
  eta: string
  detail: string
  cached: boolean
}

export interface Health {
  ok: boolean
  python: string
  onnxruntime: string
  available_providers: string[]
  active_providers: string[]
  gpu_ready: boolean
  gpu_reason: string | null
  ffmpeg: string | null
  ffmpeg_version: string | null
  ffmpeg_source: string | null
  model_dir: string
  supported_exts: string[]
  max_upload_mb: number
  max_duration_sec: number
  default_parallel: number
  max_parallel: number
}

export interface JobCreateResponse {
  id: string
  tier: string
  filename: string
  duration_sec: number
  size_bytes: number
}

/** Every stem name the UI knows how to colour and order. */
export const STEM_ORDER = ['drums', 'bass', 'other', 'vocals'] as const

export const STEM_LABELS: Record<string, string> = {
  drums: '鼓',
  bass: '贝斯',
  other: '其他',
  vocals: '人声',
  guitar: '吉他',
  piano: '钢琴',
}
