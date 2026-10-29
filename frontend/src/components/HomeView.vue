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
  (event: 'rerun', job: Job): void
  (event: 'remove', job: Job): void
}>()

const tier = ref(props.models?.default ?? 'vocals')
const mode = ref<Mode>('full')

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'full', label: '分轨 + 音高', hint: '拆出人声轨，再对它提音高' },
  { id: 'pitch', label: '只做音高', hint: '上传的已经是人声干声，跳过分轨' },
]

// 档位列表是异步来的，到了再校正默认值（服务端把默认档排在第一位）
watch(
  () => props.models?.default,
  (value) => {
    if (value) tier.value = value
  },
)

function onPick(files: File[]): void {
  emit('upload', { files, tier: tier.value, mode: mode.value })
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

    <div class="flex flex-wrap items-center gap-3">
      <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">任务</span>
      <div class="flex overflow-hidden rounded-lg border border-white/[0.08] bg-ink-800/60">
        <button
          v-for="item in MODES"
          :key="item.id"
          type="button"
          class="cursor-pointer px-3.5 py-1.5 text-[12.5px] transition-colors duration-150"
          :class="
            mode === item.id
              ? 'bg-amber/15 text-amber-bright'
              : 'text-chalk-dim hover:bg-white/[0.03] hover:text-chalk'
          "
          :title="item.hint"
          @click="mode = item.id"
        >
          {{ item.label }}
        </button>
      </div>
      <span class="text-[11px] text-chalk-mute">
        {{ MODES.find((item) => item.id === mode)?.hint }}
      </span>
    </div>

    <TierPicker v-if="mode === 'full'" v-model="tier" :models="models" />

    <p v-else class="text-[11.5px] leading-relaxed text-chalk-mute">
      跳过分轨，直接对上传的音频提取音高。请确保它是人声干声 —— 混音里的伴奏会干扰基频判断。
    </p>

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
      @rerun="emit('rerun', $event)"
      @remove="emit('remove', $event)"
    />
  </div>
</template>
