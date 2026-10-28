<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Health, Job, ModelTier } from '@/api/types'
import AppHeader from '@/components/AppHeader.vue'
import DropZone from '@/components/DropZone.vue'
import HealthDialog from '@/components/HealthDialog.vue'
import TaskBoard from '@/components/TaskBoard.vue'
import TierPicker from '@/components/TierPicker.vue'
import Workbench from '@/components/Workbench.vue'

const POLL_MS = 900
const ACTIVE = new Set(['queued', 'downloading', 'loading', 'separating'])

const health = ref<Health | null>(null)
const tiers = ref<ModelTier[]>([])
const tier = ref<string | null>(null)
const jobs = ref<Job[]>([])
const view = ref<'home' | 'workbench'>('home')
const openId = ref<string | null>(null)
const uploadError = ref<string | null>(null)
const uploading = ref(false)
const healthOpen = ref(false)

let timer: number | undefined

const hasActive = computed(() => jobs.value.some((job) => ACTIVE.has(job.stage)))
const openJob = computed(() => jobs.value.find((job) => job.id === openId.value) ?? null)
const gpuMode = computed(() => Boolean(health.value?.gpu_ready))

const weightsLabel = computed(() => {
  if (!tiers.value.length) return '权重检测中'
  const cached = tiers.value.filter((item) => item.cached).length
  return cached ? `${cached}/${tiers.value.length} 已缓存` : '权重未缓存'
})

//: The formats people actually drag in. The full list (20+) lives in the
//: health dialog; printing all of them under the drop zone is just noise.
const COMMON_EXTS = ['wav', 'flac', 'mp3', 'm4a', 'mp4']

const dropFormats = computed(() => {
  const supported = new Set(health.value?.supported_exts ?? COMMON_EXTS)
  const shown = COMMON_EXTS.filter((ext) => supported.has(ext))
  return (shown.length ? shown : COMMON_EXTS).map((ext) => ext.toUpperCase()).join(' · ')
})

const dropLimit = computed(() => {
  const minutes = Math.round((health.value?.max_duration_sec ?? 720) / 60)
  const mb = health.value?.max_upload_mb ?? 300
  const size = mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`
  return `≤ ${minutes}:00 · ≤ ${size} · 支持批量拖入`
})

function tierLabel(key: string): string {
  return tiers.value.find((item) => item.key === key)?.label ?? key
}

/* ── loading ──────────────────────────────────────────────── */

async function loadHealth(): Promise<void> {
  try {
    health.value = await api.health()
  } catch {
    /* the health chip simply stays blank */
  }
}

async function loadModels(): Promise<void> {
  try {
    tiers.value = await api.models()
    if (!tier.value) tier.value = tiers.value[0]?.key ?? null
  } catch {
    /* the drop zone still works with the server default tier */
  }
}

async function loadJobs(): Promise<void> {
  try {
    jobs.value = await api.jobs()
  } catch {
    /* transient failure: keep the previous snapshot */
  }
}

/* ── polling ──────────────────────────────────────────────── */

function stopPoll(): void {
  if (timer !== undefined) {
    window.clearTimeout(timer)
    timer = undefined
  }
}

async function poll(): Promise<void> {
  timer = undefined
  await loadJobs()
  if (hasActive.value) startPoll()
}

function startPoll(): void {
  if (timer !== undefined) return
  timer = window.setTimeout(poll, POLL_MS)
}

/* ── upload ───────────────────────────────────────────────── */

async function onFiles(files: File[]): Promise<void> {
  if (!tier.value || uploading.value) return
  uploadError.value = null
  uploading.value = true
  try {
    // Serial on purpose: the server queues jobs anyway, and a concurrent
    // burst would only race the 429 guard.
    for (const file of files) {
      await api.createJob(file, tier.value, 1)
    }
    await loadJobs()
    startPoll()
  } catch (error) {
    uploadError.value = (error as Error).message
  } finally {
    uploading.value = false
  }
}

/* ── actions ──────────────────────────────────────────────── */

async function onCancel(id: string): Promise<void> {
  await api.cancel(id).catch(() => undefined)
  await loadJobs()
  startPoll()
}

async function onRetry(id: string): Promise<void> {
  try {
    await api.retry(id)
  } catch (error) {
    uploadError.value = `重试失败：${(error as Error).message}`
    return
  }
  await loadJobs()
  startPoll()
}

async function onRemove(id: string): Promise<void> {
  await api.remove(id).catch(() => undefined)
  if (openId.value === id) backToHome()
  await loadJobs()
}

function openWorkbench(id: string): void {
  openId.value = id
  view.value = 'workbench'
  // The workbench owns playback, so polling stops until the user comes back.
  stopPoll()
}

function backToHome(): void {
  view.value = 'home'
  openId.value = null
  void loadJobs()
  if (hasActive.value) startPoll()
}

function selectTier(key: string): void {
  tier.value = key
}

onMounted(async () => {
  await Promise.all([loadHealth(), loadModels(), loadJobs()])
  if (hasActive.value) startPoll()
})

onBeforeUnmount(stopPoll)
</script>

<template>
  <div class="ambient" aria-hidden="true" />
  <div class="grain" aria-hidden="true" />

  <div class="shell">
    <AppHeader
      :health="health"
      :weights="weightsLabel"
      @health="healthOpen = true"
    />

    <main class="stage" :data-view="view">
      <section class="view" :class="{ 'is-active': view === 'home' }">
        <DropZone
          :formats="dropFormats"
          :limit="dropLimit"
          :busy="uploading"
          :error="uploadError"
          @pick="onFiles"
        />

        <TierPicker
          :tiers="tiers"
          :tier="tier"
          @select-tier="selectTier"
        />

        <div class="taskboard">
          <TaskBoard
            :jobs="jobs"
            :tier-label="tierLabel"
            @open="openWorkbench"
            @cancel="onCancel"
            @retry="onRetry"
            @remove="onRemove"
          />
          <p v-if="!jobs.length" class="empty">
            还没有任务。拖一首歌进上面的区域，或点「选择文件」。
          </p>
        </div>
      </section>

      <section class="view" :class="{ 'is-active': view === 'workbench' }">
        <Workbench
          v-if="openJob"
          :key="openJob.id"
          :job="openJob"
          @back="backToHome"
        />
      </section>
    </main>

    <footer class="foot">
      <span>本地处理 · 音频不出本机</span>
      <span class="dot-sep" />
      <span>{{ gpuMode ? 'CUDA 推理' : 'ONNX Runtime · CPU 兜底' }}</span>
    </footer>
  </div>

  <HealthDialog :open="healthOpen" :health="health" @close="healthOpen = false" />
</template>
