<script setup lang="ts">
import { AlertCircle } from 'lucide-vue-next'
import { ref, watch } from 'vue'

import type { Job, Mode, ModelList } from '@/api/types'
import DropZone from '@/components/DropZone.vue'
import TaskList from '@/components/TaskList.vue'
import TierPicker from '@/components/TierPicker.vue'

const props = defineProps<{
  jobs: Job[]
  models: ModelList | null
  notice: string
}>()

const emit = defineEmits<{
  (event: 'upload', payload: { files: File[]; tier: string; mode: Mode }): void
  (event: 'open', job: Job): void
  (event: 'cancel', job: Job): void
  (event: 'retry', job: Job): void
  (event: 'remove', job: Job): void
}>()

const tier = ref(props.models?.default ?? 'vocals')

// 干声文件名特征：vocal / vocals / 人声 / 干声 / dry / acappella
const DRY_PATTERN = /(vocal|人声|干声|dry|acap)/i

const pending = ref<File[]>([])
const askSkip = ref(false)

// 档位列表是异步来的，到了再校正默认值（服务端把默认档排在第一位）
watch(
  () => props.models?.default,
  (value) => {
    if (value) tier.value = value
  },
)

function onPick(files: File[]): void {
  // 只在「所有文件都像干声」时问一次，避免混选时频繁打断
  if (files.length > 0 && files.every((file) => DRY_PATTERN.test(file.name))) {
    pending.value = files
    askSkip.value = true
    return
  }
  emit('upload', { files, tier: tier.value, mode: 'full' })
}

function decideSkip(skip: boolean): void {
  askSkip.value = false
  const files = pending.value
  pending.value = []
  if (files.length === 0) return
  emit('upload', { files, tier: tier.value, mode: skip ? 'pitch' : 'full' })
}
</script>

<template>
  <div class="mx-auto flex w-full max-w-4xl flex-col gap-7 px-6 py-10">
    <div class="flex flex-col gap-1.5">
      <h1 class="text-[26px] font-semibold leading-tight tracking-tight text-chalk">
        一首混音，四条轨道，一条音高线
      </h1>
      <p class="max-w-xl text-[12.5px] leading-relaxed text-chalk-dim">
        拖进整首歌，本地把它拆成鼓、贝斯、其他、人声四轨，再对人声做逐帧音高提取，
        最后导出 MIDI 或可直接在 OpenUtau 打开的工程。
      </p>
    </div>

    <DropZone @pick="onPick" />

    <div v-if="askSkip" class="panel flex flex-col gap-2.5 px-4 py-3">
      <span class="text-[12.5px] leading-relaxed text-chalk">
        文件名看起来已经是人声干声
        <span class="num text-chalk-dim">（{{ pending.map((file) => file.name).join('、') }}）</span>。
        跳过分轨会更快，但如果它其实是混音，伴奏会干扰基频判断。
      </span>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn-primary" @click="decideSkip(true)">
          是干声，跳过分轨
        </button>
        <button type="button" class="btn" @click="decideSkip(false)">还是分轨</button>
      </div>
    </div>

    <TierPicker v-model="tier" :models="models" />

    <div
      v-if="notice"
      class="flex items-start gap-2 border border-red-400/30 bg-red-400/[0.06] px-3 py-2 text-[11.5px] leading-relaxed text-red-300"
    >
      <AlertCircle :size="13" class="mt-0.5 shrink-0" />
      <span>{{ notice }}</span>
    </div>

    <TaskList
      :jobs="jobs"
      @open="emit('open', $event)"
      @cancel="emit('cancel', $event)"
      @retry="emit('retry', $event)"
      @remove="emit('remove', $event)"
    />
  </div>
</template>
