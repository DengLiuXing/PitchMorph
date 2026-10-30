import type { Health, Job, JobParams, ModelList, OnsetTrack, PitchCurve, Scope } from './types'

const READ_TIMEOUT = 15_000
// 300 MB 在慢盘上要几十秒，读接口的 15 s 不够
const UPLOAD_TIMEOUT = 180_000

async function readDetail(response: Response): Promise<string> {
  // 后端返回的是中文可读文案（{detail}），原样展示，不要再包一层「请求失败」
  const text = await response.text()
  if (!text) return `请求失败（${response.status}）`
  const body = JSON.parse(text)
  return typeof body?.detail === 'string' ? body.detail : text
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = READ_TIMEOUT,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!response.ok) {
    throw new Error(await readDetail(response))
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<Health>('/api/health'),

  models: () => request<ModelList>('/api/models'),

  jobs: (scope?: Scope) =>
    request<Job[]>(scope ? `/api/jobs?scope=${scope}` : '/api/jobs'),

  /** 创建任务。参数一次给全，跑完即定稿；被拒绝时后端会回可读的中文原因 */
  upload(file: File, scope: Scope, params: JobParams): Promise<Job> {
    const form = new FormData()
    form.append('file', file)
    form.append('scope', scope)
    form.append('params', JSON.stringify(params))
    return request<Job>('/api/jobs', { method: 'POST', body: form }, UPLOAD_TIMEOUT)
  },

  /** 用新参数基于已有任务的人声轨新建分析任务（原任务与产物不动） */
  analyze(parentId: string, params: JobParams): Promise<Job> {
    return request<Job>(
      `/api/jobs/${parentId}/analyze`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      },
      UPLOAD_TIMEOUT,
    )
  },

  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  remove: (id: string) => request<void>(`/api/jobs/${id}`, { method: 'DELETE' }),

  pitch: (id: string) => request<PitchCurve>(`/api/jobs/${id}/pitch`),

  /** 起音帧。老任务没有这个文件会 404，调用方按「没有」处理 */
  onset: (id: string) => request<OnsetTrack>(`/api/jobs/${id}/onset`),

  /** 播放与下载共用同一批文件，/media 挂在 data/jobs 上 */
  mediaUrl(jobId: string, relative: string): string {
    return `/media/${jobId}/${relative}`
  },

  stemUrl(jobId: string, stem: string): string {
    // 跳过分轨的任务只有 input.wav，它在任务根目录而不是 stems/ 下
    if (stem === 'input') return api.mediaUrl(jobId, 'input.wav')
    return api.mediaUrl(jobId, `stems/${stem}.wav`)
  },

  downloadUrl(jobId: string, stem: string): string {
    if (stem === 'input') return api.mediaUrl(jobId, 'input.wav')
    return `/api/jobs/${jobId}/stems/${stem}`
  },

  /** 下载文件名带源文件主干：物凄_vocals.wav */
  downloadName(sourceName: string, suffix: string, ext = 'wav'): string {
    const stem = sourceName.replace(/\.[^.]+$/, '') || sourceName
    return `${stem}_${suffix}.${ext}`
  },
}
