<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { ref } from 'vue'

import type { NotesParams } from '@/api/types'
import MdBottomSheet from '@/components/MdBottomSheet.vue'

defineProps<{ stem: string; languages: string[]; busy?: boolean }>()
const emit = defineEmits<{ (event: 'close'): void; (event: 'submit', params: NotesParams): void }>()

/** 默认值与 OpenUtau 的转录界面一致 */
const params = ref<NotesParams>({
  tempo: 120,
  sampling_steps: 8,
  boundary_threshold: 0.1,
  boundary_radius: 1,
  score_threshold: 0.2,
  batch_size: 1,
  max_batch_duration: 60,
  language: null,
})

const STEP_OPTIONS = [1, 2, 4, 8, 16]

const field =
  'w-full rounded-md border border-outline bg-surface-container px-3 py-2 text-on-surface focus:border-primary focus:outline-none'
const label = 'md-label-medium text-on-surface-variant'

function submit(): void {
  emit('submit', { ...params.value })
}
</script>

<template>
  <MdBottomSheet :label="`提取音符参数 · ${stem}`" @close="emit('close')">
    <template #header>
      <h3 class="md-title-medium text-on-surface">提取音符 · {{ stem }}</h3>
    </template>
    <template #close-icon>
      <X :size="18" />
    </template>

    <div class="flex flex-col gap-3">
      <label class="flex flex-col gap-1">
        <span :class="label">曲速 BPM（导出 USTX / MIDI 用）</span>
        <input v-model.number="params.tempo" type="number" min="20" max="400" step="1" :class="field" />
      </label>

      <label class="flex flex-col gap-1">
        <span :class="label">采样步数（越大越准，也越慢）</span>
        <select v-model.number="params.sampling_steps" :class="field">
          <option v-for="step in STEP_OPTIONS" :key="step" :value="step">{{ step }}</option>
        </select>
      </label>

      <div class="grid grid-cols-2 gap-3">
        <label class="flex flex-col gap-1">
          <span :class="label">边界阈值</span>
          <input
            v-model.number="params.boundary_threshold"
            type="number"
            min="0.01"
            max="1"
            step="0.05"
            :class="field"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span :class="label">边界半径（帧）</span>
          <input
            v-model.number="params.boundary_radius"
            type="number"
            min="1"
            max="20"
            step="1"
            :class="field"
          />
        </label>
      </div>

      <label class="flex flex-col gap-1">
        <span :class="label">分数阈值（低于它算休止）</span>
        <input
          v-model.number="params.score_threshold"
          type="number"
          min="0.01"
          max="1"
          step="0.05"
          :class="field"
        />
      </label>

      <div class="grid grid-cols-2 gap-3">
        <label class="flex flex-col gap-1">
          <span :class="label">并行大小（每批切片数）</span>
          <input v-model.number="params.batch_size" type="number" min="1" max="16" step="1" :class="field" />
        </label>
        <label class="flex flex-col gap-1">
          <span :class="label">最大并行持续时间（秒，0 = 不限）</span>
          <input
            v-model.number="params.max_batch_duration"
            type="number"
            min="0"
            max="600"
            step="10"
            :class="field"
          />
        </label>
      </div>

      <label class="flex flex-col gap-1">
        <span :class="label">语言</span>
        <select v-model="params.language" :class="field">
          <option :value="null">通用</option>
          <option v-for="code in languages" :key="code" :value="code">{{ code }}</option>
        </select>
      </label>

      <p class="md-body-small text-on-surface-variant">
        音符边界与音高均由 GAME 模型给出（与 OpenUtau 转录同口径）；导出 OpenUtau 工程与 MIDI 时会带上 FCPE 音高线还原颤音与滑音。
      </p>

      <button
        type="button"
        class="md-button md-button--filled h-10 rounded-full bg-primary px-6 text-on-primary disabled:opacity-50"
        :disabled="busy"
        @click="submit"
      >
        {{ busy ? '正在提取…' : '开始提取' }}
      </button>
    </div>
  </MdBottomSheet>
</template>
