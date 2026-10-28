<script setup lang="ts">
import { computed } from 'vue'

import { api } from '@/api/client'
import { fmtBytes, fmtClock } from '@/api/format'
import { STEM_LABELS, type Job } from '@/api/types'

const props = defineProps<{
  jobs: Job[]
  tierLabel: (key: string) => string
}>()

const emit = defineEmits<{
  (event: 'open', id: string): void
  (event: 'cancel', id: string): void
  (event: 'retry', id: string): void
  (event: 'remove', id: string): void
}>()

const ACTIVE = new Set(['queued', 'downloading', 'loading', 'separating'])

const isRunning = (job: Job) => ACTIVE.has(job.stage)

const groups = computed(() => {
  const all = [...props.jobs].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
  return [
    { key: 'running', title: '进行中', items: all.filter(isRunning) },
    { key: 'done', title: '已完成', items: all.filter((job) => job.stage === 'done') },
    {
      key: 'other',
      title: '失败 / 已取消',
      items: all.filter((job) => !isRunning(job) && job.stage !== 'done'),
    },
  ].filter((group) => group.items.length > 0)
})

const summary = computed(() => {
  const total = props.jobs.length
  if (!total) return ''
  const running = props.jobs.filter(isRunning).length
  return running ? `${total} 个任务 · ${running} 进行中` : `${total} 个任务`
})

function stageText(job: Job): string {
  switch (job.stage) {
    case 'queued':
      return '排队中'
    case 'downloading':
      return '下载权重'
    case 'loading':
      return `加载 ${STEM_LABELS[job.stem ?? ''] ?? ''}`.trim()
    case 'separating':
      return `分离 ${STEM_LABELS[job.stem ?? ''] ?? ''}`.trim()
    case 'done':
      return '完成'
    case 'canceled':
      return '已取消'
    default:
      return '失败'
  }
}

function stateName(job: Job): string {
  return isRunning(job) ? 'running' : job.stage
}

function metaChips(job: Job): string[] {
  const chips: string[] = []
  if (job.duration_sec) chips.push(fmtClock(job.duration_sec))
  if (job.size_bytes) chips.push(fmtBytes(job.size_bytes))
  if ((job.parallel || 1) > 1) chips.push(`并发 ${job.parallel}`)
  if (job.stage === 'queued') chips.push(`等待 ${fmtClock(job.wait_sec)}`)
  else if (isRunning(job)) chips.push(`已用 ${fmtClock(job.elapsed_sec)}`)
  else if (job.elapsed_sec) chips.push(`耗时 ${fmtClock(job.elapsed_sec)}`)
  return chips
}

/**
 * The bar only exists where progress is actually measured.
 *
 * `loading` reports no progress of its own, but the job keeps the last value
 * it had, so the bar holds its width instead of collapsing or animating. A new
 * job with progress 0 gets no bar at all — an empty track reads as a glitch.
 */
function showBar(job: Job): boolean {
  if (!['downloading', 'loading', 'separating'].includes(job.stage)) return false
  return (job.progress || 0) > 0
}

function barWidth(job: Job): string {
  return `${Math.round((job.progress || 0) * 100)}%`
}

function download(id: string): void {
  window.location.href = api.downloadZip(id)
}
</script>

<template>
  <div class="taskboard-head">
    <h2>任务</h2>
    <span v-if="summary" class="mono dim">{{ summary }}</span>
  </div>
  <section v-for="group in groups" :key="group.key" class="task-group">
    <div class="task-group-head">
      <span>{{ group.title }}</span>
      <span class="mono dim">{{ group.items.length }}</span>
    </div>
    <ul class="task-list">
      <li
        v-for="job in group.items"
        :key="job.id"
        class="task"
        :data-state="stateName(job)"
      >
        <div class="task-main">
          <div class="task-line">
            <span class="task-status" :title="job.stage === 'error' ? (job.error || '') : ''">
              {{ stageText(job) }}
            </span>
            <span class="task-name" :title="job.filename">{{ job.filename }}</span>
            <span class="tag">{{ tierLabel(job.tier) }}</span>
            <span class="task-meta mono">{{ metaChips(job).join(' · ') }}</span>
          </div>
          <div v-show="showBar(job)" class="task-bar">
            <i :style="{ width: barWidth(job) }" />
          </div>
        </div>

        <div class="task-actions">
          <template v-if="isRunning(job)">
            <button
              type="button"
              class="btn btn-sm btn-ghost"
              :disabled="job.cancel_requested"
              @click="emit('cancel', job.id)"
            >{{ job.cancel_requested ? '取消中…' : '取消' }}</button>
          </template>
          <template v-else-if="job.stage === 'done'">
            <button type="button" class="btn btn-sm btn-primary" @click="emit('open', job.id)">打开</button>
            <button type="button" class="btn btn-sm btn-ghost" @click="download(job.id)">下载</button>
            <button type="button" class="btn btn-sm btn-ghost" @click="emit('remove', job.id)">删除</button>
          </template>
          <template v-else>
            <button type="button" class="btn btn-sm btn-primary" @click="emit('retry', job.id)">重试</button>
            <button type="button" class="btn btn-sm btn-ghost" @click="emit('remove', job.id)">删除</button>
          </template>
        </div>
      </li>
    </ul>
  </section>
</template>
