<script setup lang="ts">
import { Loader2, Maximize2, X } from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'

import type { PitchCurve } from '@/api/types'
import MdBottomSheet from '@/components/MdBottomSheet.vue'
import MdChip from '@/components/MdChip.vue'
import MdIconButton from '@/components/MdIconButton.vue'
import { useCanvas } from '@/composables/useCanvas'
import { drawPitch, drawPianoRoll, pitchRange, ROLL_RANGE, type PitchWindow, type RollNote } from '@/lib/canvas'
import { midiToName } from '@/lib/format'

/** 固定 5 s 窗口铺满整块监视器：与时间轴缩放无关，播放时内容从右往左滑 */
const WINDOW_SEC = 5

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
  /** 音符提取完成后的音符块（全屏钢琴卷帘用），null = 还没提取 */
  notes: RollNote[] | null
  /** 底部进度条高度：全屏层只盖到进度条上沿，两者并列 */
  bottomInset: number
}>()

const emit = defineEmits<{ (event: 'close'): void }>()

const expanded = ref(false)

const range = computed(() => pitchRange(props.curve))
const rangeLabel = computed(() =>
  props.curve ? `${midiToName(range.value.low)}–${midiToName(range.value.high)}` : '',
)

/** 以播放头为中心取 5 秒；贴住开头/结尾时窗口夹紧不越界 */
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

const { canvas: rollCanvas, paint: paintRoll } = useCanvas((ctx, size) => {
  drawPianoRoll(
    ctx,
    size,
    props.curve ?? { hop_ms: 10, sample_rate: 16000, times: [], f0: [], voiced: [], confidence: [] },
    props.color,
    win.value,
    props.notes ?? [],
  )
})

// position 每帧都在变：播放时曲线跟着窗口滑，无需额外定时器
watch(
  () => [props.curve, props.position, props.duration, props.color, props.label, expanded.value],
  () => {
    paint()
    if (expanded.value) paintRoll()
  },
  { immediate: true },
)

// v-if 展开的那一帧 canvas ref 还没绑上，等 DOM 挂载完再画第一笔
watch(expanded, async (value) => {
  if (!value) return
  await nextTick()
  paintRoll()
})
</script>

<template>
  <MdBottomSheet label="音高监视器" @close="emit('close')">
    <template #header>
      <i class="lane-swatch" :style="{ background: color }" aria-hidden="true" />
      <span class="md-title-small text-on-surface">{{ label }} · 音高</span>
      <MdChip variant="flat">±2.5 s 滑动窗口</MdChip>
      <MdChip v-if="rangeLabel && !expanded" variant="tertiary">{{ rangeLabel }}</MdChip>
      <MdChip v-if="expanded" variant="tertiary">C3–C6</MdChip>
      <span class="md-label-medium ml-1 text-on-surface-variant">
        {{ dimmed ? '该轨已静音' : '跟随播放头' }}
      </span>
      <MdIconButton
        small
        :label="notes ? '全屏钢琴卷帘（含音符）' : '全屏音高线'"
        @click="expanded = !expanded"
      >
        <Maximize2 :size="18" />
      </MdIconButton>
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

  <!-- 全屏钢琴卷帘：固定 C3-C6 音域，音符提取后显示 OpenUtau 风格的音符块；
       bottom 让出进度条的高度，两者并列 -->
  <div
    v-if="expanded"
    class="fixed inset-x-0 top-0 z-50 flex flex-col bg-surface-high"
    :style="{ bottom: `${Math.max(0, bottomInset)}px` }"
  >
    <div class="flex items-center gap-2 px-4 py-3">
      <i class="lane-swatch" :style="{ background: color }" aria-hidden="true" />
      <span class="md-title-medium text-on-surface">{{ label }} · 音高线</span>
      <MdChip variant="flat">±2.5 s 滑动窗口</MdChip>
      <MdChip variant="tertiary">C3–C6</MdChip>
      <MdChip v-if="notes" variant="tertiary">{{ notes.length }} 个音符</MdChip>
      <span class="md-label-medium text-on-surface-variant">
        {{ notes ? '跟随播放头，Esc 退出' : '跟随播放头 · 提取音符后显示音符块' }}
      </span>
      <span class="flex-1" />
      <MdIconButton small label="退出全屏 (Esc)" @click="expanded = false">
        <X :size="20" />
      </MdIconButton>
    </div>
    <div class="relative min-h-0 flex-1">
      <canvas ref="rollCanvas" class="absolute inset-0 h-full w-full" />
      <div v-if="status" class="monitor-state">
        <span
          class="md-label-medium flex items-center gap-2 rounded-md-sm border border-outline-variant bg-surface-container px-3 py-1.5 text-on-surface-variant"
        >
          <Loader2 v-if="pending" :size="14" class="animate-spin text-primary" />
          {{ status }}
        </span>
      </div>
    </div>
  </div>
</template>
