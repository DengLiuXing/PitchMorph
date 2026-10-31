<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'
import { computed, watch } from 'vue'

import type { PitchCurve } from '@/api/types'
import MdBottomSheet from '@/components/MdBottomSheet.vue'
import MdChip from '@/components/MdChip.vue'
import { useCanvas } from '@/composables/useCanvas'
import { drawPitch, pitchRange, type PitchWindow } from '@/lib/canvas'
import { midiToName } from '@/lib/format'

/** 固定 10 s 窗口铺满整块监视器：与时间轴缩放无关，播放时内容从右往左滑 */
const WINDOW_SEC = 10

const props = defineProps<{
  label: string
  color: string
  curve: PitchCurve | null
  duration: number
  position: number
  /** 该轨被 solo 挤掉或静音：整块退到背景 */
  dimmed: boolean
  pending: boolean
  percent: number
  failure: string | null
}>()

const emit = defineEmits<{ (event: 'close'): void }>()

const range = computed(() => pitchRange(props.curve))
const rangeLabel = computed(() =>
  props.curve ? `${midiToName(range.value.low)}–${midiToName(range.value.high)}` : '',
)

/** 以播放头为中心取 10 秒；贴住开头/结尾时窗口夹紧不越界 */
const win = computed<PitchWindow>(() => {
  const half = WINDOW_SEC / 2
  let from = props.position - half
  let to = props.position + half
  if (from < 0) {
    to -= from
    from = 0
  }
  if (props.duration > 0 && to > props.duration) {
    from -= to - props.duration
    to = props.duration
    if (from < 0) from = 0
  }
  return { from, to: Math.max(to, from + 0.001), cursor: props.position }
})

const status = computed(() => {
  if (props.curve) return ''
  if (props.pending) return `提取音高中 ${props.percent}%`
  if (props.failure) return `音高提取失败：${props.failure}`
  return '暂无曲线'
})

const { canvas, paint } = useCanvas((ctx, size) => {
  drawPitch(
    ctx,
    size,
    props.curve ?? { hop_ms: 10, sample_rate: 16000, times: [], f0: [], voiced: [], confidence: [] },
    range.value,
    props.color,
    win.value,
  )
})

// position 每帧都在变：播放时曲线跟着窗口滑，无需额外定时器
watch(
  () => [props.curve, props.position, props.duration, props.color, props.label],
  paint,
  { immediate: true },
)
</script>

<template>
  <MdBottomSheet label="音高监视器" @close="emit('close')">
    <template #header>
      <i class="lane-swatch" :style="{ background: color }" aria-hidden="true" />
      <span class="md-title-small text-on-surface">{{ label }} · 音高</span>
      <MdChip variant="flat">±5 s 滑动窗口</MdChip>
      <MdChip v-if="rangeLabel" variant="tertiary">{{ rangeLabel }}</MdChip>
      <span class="md-label-medium ml-1 text-on-surface-variant">
        {{ dimmed ? '该轨已静音' : '跟随播放头' }}
      </span>
    </template>

    <template #close-icon>
      <X :size="18" />
    </template>

    <div class="monitor-body" :style="{ opacity: dimmed ? 0.5 : 1 }">
      <canvas ref="canvas" class="monitor-canvas" />

      <div v-if="status" class="monitor-state">
        <span
          class="md-label-medium flex items-center gap-2 rounded-md-sm border border-outline-variant bg-surface-container px-3 py-1.5 text-on-surface-variant"
        >
          <Loader2 v-if="pending" :size="14" class="animate-spin text-primary" />
          {{ status }}
        </span>
      </div>
    </div>
  </MdBottomSheet>
</template>
