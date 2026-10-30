<script setup lang="ts">
import { AlertCircle } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

import type { Job, JobParams, ModelList, Scope } from '@/api/types'
import CreateJobDialog from '@/components/CreateJobDialog.vue'
import DropZone from '@/components/DropZone.vue'
import TaskList from '@/components/TaskList.vue'

const props = defineProps<{
  jobs: Job[]
  models: ModelList | null
  notice: string
  /** 非空表示这次弹窗是「从已有任务派生分析任务」 */
  deriveFrom?: string | null
}>()

const emit = defineEmits<{
  (event: 'upload', payload: { files: File[]; scope: Scope; params: JobParams }): void
  (event: 'derive', payload: { params: JobParams }): void
  (event: 'cancel-derive'): void
  (event: 'open', job: Job): void
  (event: 'cancel', job: Job): void
  (event: 'retry', job: Job): void
  (event: 'remove', job: Job): void
}>()

// 历史分成两张：分离（含 AB）与 分析（含 AB）。一个 AB 任务两边都该看见。
const tab = ref<'separate' | 'analyze'>('separate')
const TABS = [
  { id: 'separate' as const, label: '分离音轨' },
  { id: 'analyze' as const, label: '音高与音符' },
]

function inTab(job: Job, id: 'separate' | 'analyze'): boolean {
  return id === 'separate' ? job.scope !== 'analyze' : job.scope !== 'separate'
}

const visibleJobs = computed(() => props.jobs.filter((job) => inTab(job, tab.value)))

// 拖进文件后先弹窗问参数，确认才真正入队
const pending = ref<File[]>([])
const dialogOpen = ref(false)
const dialogDerive = ref(false)
const dialogKey = ref(0)

watch(
  () => props.deriveFrom,
  (value) => {
    if (!value) return
    pending.value = []
    dialogDerive.value = true
    dialogKey.value += 1
    dialogOpen.value = true
  },
)

function onPick(files: File[]): void {
  if (files.length === 0) return
  pending.value = files
  dialogDerive.value = false
  dialogKey.value += 1 // 每次重新打开都重置参数
  dialogOpen.value = true
}

function submit(payload: { scope: Scope; params: JobParams }): void {
  const files = pending.value
  const deriving = dialogDerive.value
  pending.value = []
  dialogOpen.value = false
  if (deriving) {
    emit('derive', { params: payload.params })
    return
  }
  if (files.length === 0) return
  emit('upload', { files, scope: payload.scope, params: payload.params })
}

function cancel(): void {
  const deriving = dialogDerive.value
  pending.value = []
  dialogOpen.value = false
  dialogDerive.value = false
  if (deriving) emit('cancel-derive')
}
</script>

<template>
  <div class="mx-auto flex w-full max-w-4xl flex-col gap-7 px-6 py-10">
    <div class="flex flex-col gap-1.5">
      <h1 class="text-[26px] font-semibold leading-tight tracking-tight text-chalk">
        分离音轨，或者提取音高与音符
      </h1>
      <p class="max-w-xl text-[12.5px] leading-relaxed text-chalk-dim">
        两件事可以分开做：分轨只出四条 WAV；分析只对人声干声出音高线与音符。
        拖进文件后弹出参数窗，一次定完；想换参数就再建一个任务，两个任务的结果可以并排比较。
      </p>
    </div>

    <DropZone @pick="onPick" />

    <div
      v-if="notice"
      class="flex items-start gap-2 border border-red-400/30 bg-red-400/[0.06] px-3 py-2 text-[11.5px] leading-relaxed text-red-300"
    >
      <AlertCircle :size="13" class="mt-0.5 shrink-0" />
      <span>{{ notice }}</span>
    </div>

    <div class="flex items-center gap-1">
      <button
        v-for="item in TABS"
        :key="item.id"
        type="button"
        class="cursor-pointer px-2.5 py-1 text-[11.5px] transition-colors duration-150"
        :class="
          tab === item.id
            ? 'border-b border-amber text-amber-bright'
            : 'border-b border-transparent text-chalk-mute hover:text-chalk'
        "
        @click="tab = item.id"
      >
        {{ item.label }}
        <span class="num ml-1 text-chalk-mute">
          {{ jobs.filter((job) => inTab(job, item.id)).length }}
        </span>
      </button>
    </div>

    <TaskList
      :jobs="visibleJobs"
      @open="emit('open', $event)"
      @cancel="emit('cancel', $event)"
      @retry="emit('retry', $event)"
      @remove="emit('remove', $event)"
    />

    <CreateJobDialog
      v-if="dialogOpen"
      :key="dialogKey"
      :files="pending"
      :models="models"
      :derive="dialogDerive"
      @submit="submit"
      @cancel="cancel"
    />
  </div>
</template>
