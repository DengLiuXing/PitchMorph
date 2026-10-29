<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Health, Job, ModelList } from '@/api/types'
import { isActive } from '@/api/types'
import AppHeader from '@/components/AppHeader.vue'
import HomeView from '@/components/HomeView.vue'
import Workbench from '@/components/Workbench.vue'

const POLL_INTERVAL = 900 // 低于 500 ms 请求翻倍，高于 1500 ms 取消反馈迟钝

const view = ref<'home' | 'workbench'>('home')
const jobs = ref<Job[]>([])
const health = ref<Health | null>(null)
const models = ref<ModelList | null>(null)
const notice = ref('')
const currentId = ref<string | null>(null)
const workbench = ref<InstanceType<typeof Workbench> | null>(null)

let timer: number | undefined

const current = computed(
  () => jobs.value.find((job) => job.id === currentId.value) ?? null,
)

async function loadJobs(): Promise<void> {
  jobs.value = await api.jobs()
  window.clearTimeout(timer)
  // 列表状态只来自服务端快照；有活跃任务才继续轮询，完成即停
  if (jobs.value.some((job) => isActive(job.stage))) {
    timer = window.setTimeout(() => void loadJobs(), POLL_INTERVAL)
  }
}

async function guard(action: () => Promise<void>): Promise<void> {
  try {
    await action()
  } catch (error) {
    notice.value = (error as Error).message
  }
}

async function upload(payload: { files: File[]; tier: string; mode: string }): Promise<void> {
  notice.value = ''
  for (const file of payload.files) {
    // 串行上传：服务端本来就是单 worker 排队，并发只会撞限流
    await guard(async () => {
      const job = await api.upload(file, payload.tier, payload.mode)
      jobs.value = [job, ...jobs.value]
    })
  }
  await guard(loadJobs)
}



function open(job: Job): void {
  currentId.value = job.id
  view.value = 'workbench'
}

function back(): void {
  // 视图切换只是 display:none，音频元素还在跑，必须主动停
  workbench.value?.stop()
  view.value = 'home'
  void guard(loadJobs)
}

function cancel(job: Job): void {
  void guard(async () => {
    await api.cancel(job.id)
    await loadJobs()
  })
}

function retry(job: Job): void {
  void guard(async () => {
    const fresh = await api.retry(job.id)
    jobs.value = [fresh, ...jobs.value]
    await loadJobs()
  })
}

function remove(job: Job): void {
  void guard(async () => {
    await api.remove(job.id)
    if (currentId.value === job.id) {
      currentId.value = null
      view.value = 'home'
    }
    await loadJobs()
  })
}

onMounted(() => {
  void guard(async () => {
    health.value = await api.health()
    models.value = await api.models()
    await loadJobs()
  })
})

onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="ambient" aria-hidden="true" />
  <div class="grain" aria-hidden="true" />

  <div class="relative z-[1] flex min-h-screen flex-col">
    <AppHeader :health="health" :view="view" @back="back" />

    <main class="flex-1 pt-16">
      <!-- 视图切换用类，禁用 v-show：它移除 inline display 后，.view{display:none} 会继续生效 -->
      <section class="view" :class="{ 'is-active': view === 'home' }">
        <HomeView
          :jobs="jobs"
          :models="models"
          :notice="notice"
          @upload="upload"
          @open="open"
          @cancel="cancel"
          @retry="retry"
          @remove="remove"
        />
      </section>

      <section class="view" :class="{ 'is-active': view === 'workbench' }">
        <Workbench ref="workbench" v-if="current" :job="current" />
        <div v-else class="px-6 py-16 text-center text-[12px] text-chalk-mute">
          任务已不在列表里，请回到队列重新选择。
        </div>
      </section>
    </main>

    <!-- 工作台要占满一屏，footer 只在队列页出现 -->
    <footer
      v-if="view === 'home'"
      class="flex items-center gap-3 border-t border-white/[0.06] px-6 py-3 text-[11px] text-chalk-mute"
    >
      <span>本地处理 · 音频不出本机</span>
      <span class="h-3 w-px bg-white/10" />
      <span class="num">{{ health ? 'PyTorch · CUDA 可用' : 'PyTorch · CPU 兜底' }}</span>
      <span class="ml-auto num">PitchMorph v2</span>
    </footer>
  </div>
</template>
