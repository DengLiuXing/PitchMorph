// Thin fetch wrapper around the FastAPI surface in app/main.py.

import type { Health, Job, JobCreateResponse, ModelTier } from './types'

const READ_TIMEOUT_MS = 15_000
const UPLOAD_TIMEOUT_MS = 180_000

/** Turn a non-2xx response into an Error worth showing the user. */
async function failure(res: Response): Promise<Error> {
  let message = `请求失败（HTTP ${res.status}）`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body?.detail === 'string') {
      message = body.detail
    } else if (Array.isArray(body?.detail)) {
      // FastAPI 422: [{ loc, msg, type }, …] — surface the messages, not the paths.
      const messages = body.detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : '',
        )
        .filter(Boolean)
      if (messages.length) message = messages.join('；')
    }
  } catch {
    /* non-JSON body: the status message is all we have */
  }
  return new Error(message)
}

function timeout(ms: number): AbortSignal | undefined {
  // Not every engine ships AbortSignal.timeout; falling back to "no timeout"
  // keeps old browsers working instead of throwing on every request.
  return typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(ms) : undefined
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = READ_TIMEOUT_MS): Promise<T> {
  const res = await fetch(path, { ...init, signal: init?.signal ?? timeout(timeoutMs) })
  if (!res.ok) throw await failure(res)
  return (await res.json()) as T
}

/** POST/DELETE endpoints answer with 2xx and no body worth parsing. */
async function command(path: string, method: 'POST' | 'DELETE'): Promise<void> {
  const res = await fetch(path, { method, signal: timeout(READ_TIMEOUT_MS) })
  if (!res.ok) throw await failure(res)
}

export const api = {
  health: () => request<Health>('/api/health'),
  models: () => request<ModelTier[]>('/api/models'),
  jobs: () => request<Job[]>('/api/jobs'),

  /**
   * `parallel` is the experimental concurrency knob. Left out when undefined so
   * the server keeps its own default (`PITCHMORPH_PARALLEL`).
   */
  createJob(file: File, tier: string, parallel?: number): Promise<JobCreateResponse> {
    const form = new FormData()
    form.append('file', file)
    form.append('tier', tier)
    if (parallel !== undefined) form.append('parallel', String(parallel))
    return request<JobCreateResponse>(
      '/api/jobs',
      { method: 'POST', body: form },
      UPLOAD_TIMEOUT_MS,
    )
  },

  cancel: (id: string) => command(`/api/jobs/${id}/cancel`, 'POST'),
  retry: (id: string) => command(`/api/jobs/${id}/retry`, 'POST'),
  remove: (id: string) => command(`/api/jobs/${id}`, 'DELETE'),

  downloadZip: (id: string) => `/api/jobs/${id}/download`,
}
