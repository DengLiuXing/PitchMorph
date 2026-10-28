// Thin fetch wrapper around the FastAPI surface in app/main.py.

import type { Health, Job, JobCreateResponse, ModelTier } from './types'

async function failure(res: Response): Promise<Error> {
  let message = `请求失败（HTTP ${res.status}）`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body?.detail === 'string') message = body.detail
  } catch {
    /* non-JSON body: the status message is all we have */
  }
  return new Error(message)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw await failure(res)
  return (await res.json()) as T
}

/** POST/DELETE endpoints answer with 2xx and no body worth parsing. */
async function command(path: string, method: 'POST' | 'DELETE'): Promise<void> {
  const res = await fetch(path, { method })
  if (!res.ok) throw await failure(res)
}

export const api = {
  health: () => request<Health>('/api/health'),
  models: () => request<ModelTier[]>('/api/models'),
  jobs: () => request<Job[]>('/api/jobs'),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),

  createJob(file: File, tier: string, parallel: number): Promise<JobCreateResponse> {
    const form = new FormData()
    form.append('file', file)
    form.append('tier', tier)
    form.append('parallel', String(parallel))
    return request<JobCreateResponse>('/api/jobs', { method: 'POST', body: form })
  },

  cancel: (id: string) => command(`/api/jobs/${id}/cancel`, 'POST'),
  retry: (id: string) => command(`/api/jobs/${id}/retry`, 'POST'),
  remove: (id: string) => command(`/api/jobs/${id}`, 'DELETE'),

  downloadZip: (id: string) => `/api/jobs/${id}/download`,
}
