import type { EngineStatus, JobMeta, Mode, Peaks, PitchCurve } from './types'

const READ_TIMEOUT = 15_000
// 200 MB 在慢盘上要几十秒，读接口的 15 s 不够
const UPLOAD_TIMEOUT = 300_000

async function readDetail(response: Response): Promise<string> {
  // 后端错误文案已经是中文可读的，原样抛给 UI，不要再包一层
  const text = await response.text()
  if (!text) return `请求失败（${response.status}）`
  const body = JSON.parse(text) as { detail?: unknown }
  if (typeof body.detail === 'string') return body.detail
  return text
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = READ_TIMEOUT): Promise<T> {
  const response = await fetch(path, { ...init, signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok) throw new Error(await readDetail(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  status: () => request<EngineStatus>('/api/status'),

  jobs: () => request<JobMeta[]>('/api/jobs'),

  job: (id: string) => request<JobMeta>(`/api/jobs/${id}`),

  create(file: File, mode: Mode): Promise<{ id: string }> {
    const form = new FormData()
    form.append('file', file)
    form.append('mode', mode)
    return request<{ id: string }>('/api/jobs', { method: 'POST', body: form }, UPLOAD_TIMEOUT)
  },

  cancel: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  remove: (id: string) => request<unknown>(`/api/jobs/${id}`, { method: 'DELETE' }),

  peaks: (id: string, stem: string) => request<Peaks>(`/api/jobs/${id}/peaks/${stem}`),

  /** 起音高任务。已经在跑时后端直接回当前状态，可以安全重复调用 */
  startPitch: (id: string, stem: string) =>
    request<{ ok: boolean; state: string }>(`/api/jobs/${id}/pitch/${stem}`, { method: 'POST' }),

  /** 还没算完时后端只回 {state}，没有曲线本身那些字段 */
  pitch: (id: string, stem: string) =>
    request<PitchCurve & { state?: string }>(`/api/jobs/${id}/pitch/${stem}`),

  audioUrl: (id: string, name: string) => `/api/jobs/${id}/audio/${name}`,

  stemUrl: (id: string, stem: string) => `/api/jobs/${id}/audio/${stem}`,

  exportUrl: (id: string) => `/api/jobs/${id}/export`,

  /** 下载文件名规范 (源文件名)_(音轨类型).wav，与后端 stem_filename 一致 */
  downloadName(filename: string, stem: string): string {
    const base = filename.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|\s]+/g, '_') || 'track'
    return `${base}_${stem}.wav`
  },

  exportName(filename: string): string {
    const base = filename.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|\s]+/g, '_') || 'track'
    return `${base}_stems.zip`
  },
}
