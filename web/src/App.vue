<script setup lang="ts">
import { ArrowLeft, Cpu, Download, Square, X, Zap } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { EngineStatus, JobMeta, Mode } from '@/api/types'
import { MODE_LABELS, isActive } from '@/api/types'
import HomeView from '@/components/HomeView.vue'
import MdChip from '@/components/MdChip.vue'
import MdIconButton from '@/components/MdIconButton.vue'
import MdTopAppBar from '@/components/MdTopAppBar.vue'
import Workbench from '@/components/Workbench.vue'
import { useJobs } from '@/composables/useJobs'
import { formatTime } from '@/lib/format'

/** #job/<id> 直接进详情页：刷新不丢当前任务 */
const HASH_PREFIX = '#job/'

const { jobs, error, uploading, refresh, upload, cancel, remove, find, clearError } = useJobs()

const status = ref<EngineStatus | null>(null)
const view = ref<'home' | 'workbench'>('home')
const currentId = ref<string | null>(null)
const workbench = ref<InstanceType<typeof Workbench> | null>(null)

const current = computed(() => find(currentId.value))
const onGpu = computed(() => status.value?.ep === 'cuda')

const barTitle = computed(() => (view.value === 'workbench' ? (current.value?.filename ?? '任务') : 'PitchMorph'))
const barSubtitle = computed(() => {
  if (view.value !== 'workbench' || !current.value) return '本地分轨 · 音高提取 · 音轨导出'
  const job = current.value
  const parts = [MODE_LABELS[job.mode] ?? job.mode]
  if (job.duration) parts.push(formatTime(job.duration, true))
  if (job.stems.length) parts.push(`${job.stems.length} 轨`)
  if (job.ep) parts.push(job.ep)
  return parts.join(' · ')
})

function open(job: JobMeta): void {
  currentId.value = job.id
  view.value = 'workbench'
  window.location.hash = `${HASH_PREFIX}${job.id}`
}

function back(): void {
  // 视图切换只是 display:none，音频元素还活着，必须主动停
  workbench.value?.stop()
  view.value = 'home'
  window.location.hash = ''
  void refresh()
}

async function onUpload(payload: { files: File[]; mode: Mode }): Promise<void> {
  // 传完留在队列页：进度在卡片上看，别把人扔进一个还没有轨道的详情页
  await upload(payload.files, payload.mode)
}

async function onRemove(job: JobMeta): Promise<void> {
  if (currentId.value === job.id) back()
  await remove(job.id)
}

async function cancelCurrent(): Promise<void> {
  if (!current.value) return
  await cancel(current.value.id)
}

onMounted(() => {
  const id = window.location.hash.startsWith(HASH_PREFIX)
    ? window.location.hash.slice(HASH_PREFIX.length)
    : ''
  if (id) {
    currentId.value = id
    view.value = 'workbench'
  }
  api
    .status()
    .then((value) => (status.value = value))
    .catch((issue) => console.error('状态接口失败', issue))
  void refresh()
})
</script>

<template>
  <div class="relative z-10 flex h-full flex-col">
    <MdTopAppBar :title="barTitle" :subtitle="barSubtitle">
      <template #leading>
        <MdIconButton v-if="view === 'workbench'" label="返回任务队列" @click="back()">
          <ArrowLeft :size="20" />
        </MdIconButton>
        <div v-else class="flex h-10 w-10 items-center justify-center rounded-full bg-primary-container">
          <svg width="20" height="20" viewBox="0 0 22 22" aria-hidden="true" class="text-on-primary-container">
            <g fill="currentColor">
              <rect x="2" y="9" width="1.8" height="4" rx="0.9" opacity="0.5" />
              <rect x="5.6" y="6" width="1.8" height="10" rx="0.9" opacity="0.75" />
              <rect x="9.2" y="3" width="1.8" height="16" rx="0.9" />
              <rect x="12.8" y="6.5" width="1.8" height="9" rx="0.9" opacity="0.75" />
              <rect x="16.4" y="8.5" width="1.8" height="5" rx="0.9" opacity="0.5" />
            </g>
          </svg>
        </div>
      </template>

      <template #actions>
        <MdChip :variant="onGpu ? 'primary' : 'flat'" :title="status?.fallback_reason ?? '推理后端'">
          <template #icon>
            <Zap v-if="onGpu" :size="12" />
            <Cpu v-else :size="12" />
          </template>
          {{ onGpu ? 'CUDA' : 'CPU' }}
        </MdChip>

        <template v-if="view === 'workbench' && current">
          <a
            v-if="current.stems.length"
            class="md-btn md-btn--tonal md-btn--compact"
            :href="api.exportUrl(current.id)"
            :download="api.exportName(current.filename)"
            title="打包下载全部音轨（不含音高与音符）"
          >
            <Download :size="16" />
            导出
          </a>
          <MdIconButton v-if="isActive(current.state)" label="取消任务" @click="cancelCurrent()">
            <Square :size="18" />
          </MdIconButton>
        </template>
      </template>
    </MdTopAppBar>

    <main class="relative min-h-0 flex-1">
      <section v-if="view === 'home'" class="h-full overflow-y-auto md-scroll">
        <HomeView
          :jobs="jobs"
          :uploading="uploading"
          @upload="onUpload"
          @open="open"
          @cancel="cancel($event.id)"
          @remove="onRemove"
        />
      </section>

      <section v-else class="h-full">
        <!-- 换任务就整块重建：播放器、缩放、监视器都该归零 -->
        <Workbench v-if="current" :key="current.id" ref="workbench" :job="current" @refresh="refresh" />
        <div v-else class="grid h-full place-items-center text-on-surface-variant">
          <p class="md-body-medium">任务已不在列表里，回队列重新选择。</p>
        </div>
      </section>
    </main>

    <!-- MD3 snackbar：错误就地提示，不打断当前操作 -->
    <div
      v-if="error"
      class="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4"
      aria-live="polite"
    >
      <div
        class="animate-rise-in pointer-events-auto flex max-w-[560px] items-center gap-3 rounded-[4px] bg-inverse-surface px-4 py-3 shadow-md-3"
      >
        <p class="md-body-medium text-inverse-on-surface">{{ error }}</p>
        <button
          type="button"
          class="md-label-large ml-2 shrink-0 text-inverse-primary"
          @click="clearError()"
        >
          知道了
        </button>
        <MdIconButton label="关闭提示" @click="clearError()">
          <X :size="16" />
        </MdIconButton>
      </div>
    </div>
  </div>
</template>
