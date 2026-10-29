import type { Health, Job, ModelList, PitchCurve, StemName } from './types'

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

  jobs: () => request<Job[]>('/api/jobs'),

  upload(file: File, tier: string, mode: string): Promise<Job> {
    const form = new FormData()
    form.append('file', file)
    form.append('tier', tier)
    form.append('mode', mode)
    return request<Job>('/api/jobs', { method: 'POST', body: form }, UPLOAD_TIMEOUT)
  },

  /** 只重跑音高：复用已有的 stems/vocals.wav，不重新分离 */
  rerunPitch: (id: string) =>
    request<Job>(`/api/jobs/${id}/pitch`, { method: 'POST' }, UPLOAD_TIMEOUT),

  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  remove: (id: string) => request<void>(`/api/jobs/${id}`, { method: 'DELETE' }),

  pitch: (id: string) => request<PitchCurve>(`/api/jobs/${id}/pitch`),

  /** 播放与下载共用同一批文件，/media 挂在 data/jobs 上 */
  mediaUrl(jobId: string, relative: string): string {
    return `/media/${jobId}/${relative}`
  },

  stemUrl(jobId: string, stem: StemName): string {
    return api.mediaUrl(jobId, `stems/${stem}.wav`)
  },

  downloadUrl(jobId: string, stem: StemName): string {
    return `/api/jobs/${jobId}/stems/${stem}`
  },

  /** 下载文件名带源文件主干：物凄_vocals.wav */
  downloadName(sourceName: string, suffix: string, ext = 'wav'): string {
    const stem = sourceName.replace(/\.[^.]+$/, '') || sourceName
    return `${stem}_${suffix}.${ext}`
  },
}
