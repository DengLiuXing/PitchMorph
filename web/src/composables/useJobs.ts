import { onBeforeUnmount, ref } from 'vue'

import { api } from '@/api/client'
import type { JobMeta, Mode } from '@/api/types'
import { needsPolling } from '@/api/types'

/** 低于 500 ms 请求翻倍，高于 1500 ms 取消/进度反馈迟钝 */
const POLL_INTERVAL = 900

/**
 * 任务列表的唯一数据源：条件轮询（只在有活跃任务或排队中的音高时继续）。
 * 详情页也从这里取 job，避免第二套状态源。
 */
export function useJobs() {
  const jobs = ref<JobMeta[]>([])
  const error = ref('')
  const uploading = ref(0)

  let timer: number | undefined
  let alive = true

  async function refresh(): Promise<void> {
    try {
      jobs.value = await api.jobs()
    } catch (issue) {
      error.value = (issue as Error).message
      console.error('任务列表刷新失败', issue)
    }
    window.clearTimeout(timer)
    if (!alive) return
    if (jobs.value.some(needsPolling)) timer = window.setTimeout(() => void refresh(), POLL_INTERVAL)
  }

  /** 串行上传：后端本来就是单 worker 排队，并发只会撞限流 */
  async function upload(files: File[], mode: Mode): Promise<void> {
    uploading.value = files.length
    for (const file of files) {
      try {
        await api.create(file, mode)
      } catch (issue) {
        error.value = (issue as Error).message
        console.error('上传失败', issue)
      }
      uploading.value = Math.max(0, uploading.value - 1)
    }
    uploading.value = 0
    await refresh()
  }

  async function cancel(id: string): Promise<void> {
    try {
      await api.cancel(id)
    } catch (issue) {
      error.value = (issue as Error).message
      console.error('取消失败', issue)
    }
    await refresh()
  }

  async function remove(id: string): Promise<void> {
    try {
      await api.remove(id)
    } catch (issue) {
      error.value = (issue as Error).message
      console.error('删除失败', issue)
    }
    await refresh()
  }

  function find(id: string | null): JobMeta | null {
    return id ? (jobs.value.find((job) => job.id === id) ?? null) : null
  }

  function clearError(): void {
    error.value = ''
  }

  onBeforeUnmount(() => {
    alive = false
    window.clearTimeout(timer)
  })

  return { jobs, error, uploading, refresh, upload, cancel, remove, find, clearError }
}
