<script setup lang="ts">
import { Pause, Play } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import MdChip from '@/components/MdChip.vue'
import { formatTime } from '@/lib/format'

const props = defineProps<{
  playing: boolean
  position: number
  duration: number
  /** 例如「人声 solo」——底部条要说明当前听到的是哪几轨 */
  hint: string
}>()

const emit = defineEmits<{
  (event: 'toggle'): void
  (event: 'seek', seconds: number): void
}>()

const scrub = ref<number | null>(null)
const display = computed(() => scrub.value ?? props.position)
const ratio = computed(() =>
  props.duration > 0 ? Math.min(1, Math.max(0, display.value / props.duration)) : 0,
)

let dragging = false

function secondsAt(event: PointerEvent): number {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width === 0) return 0
  const value = (event.clientX - rect.left) / rect.width
  return Math.min(1, Math.max(0, value)) * props.duration
}

function down(event: PointerEvent): void {
  const element = event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  dragging = true
  scrub.value = secondsAt(event)
}

function move(event: PointerEvent): void {
  if (dragging) scrub.value = secondsAt(event)
}

/** 松手才真正 seek：拖动中每帧改 currentTime 会让每个 <audio> 重发 Range 请求 */
function up(event: PointerEvent): void {
  if (!dragging) return
  dragging = false
  const element = event.currentTarget as HTMLElement
  if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId)
  scrub.value = secondsAt(event)
  emit('seek', scrub.value)
  scrub.value = null
}

function keyStep(delta: number): void {
  emit('seek', Math.min(props.duration, Math.max(0, props.position + delta)))
}
</script>

<template>
  <div class="transport shrink-0">
    <button
      type="button"
      class="md-fab"
      :aria-label="playing ? '暂停（空格）' : '播放（空格）'"
      :title="playing ? '暂停（空格）' : '播放（空格）'"
      @click="emit('toggle')"
    >
      <Pause v-if="playing" :size="20" fill="currentColor" />
      <Play v-else :size="20" fill="currentColor" style="margin-left: 2px" />
    </button>

    <span class="md-label-large md-num w-14 text-on-surface">{{ formatTime(display, true) }}</span>

    <div
      class="seek"
      role="slider"
      tabindex="0"
      aria-label="播放进度"
      :aria-valuemin="0"
      :aria-valuemax="Math.round(duration)"
      :aria-valuenow="Math.round(display)"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @keydown.left.prevent="keyStep(-5)"
      @keydown.right.prevent="keyStep(5)"
    >
      <div class="seek-fill" :style="{ width: `${ratio * 100}%` }" />
      <div class="seek-knob" :style="{ left: `${ratio * 100}%` }" />
    </div>

    <span class="md-label-medium md-num w-14 text-right text-on-surface-variant">
      {{ formatTime(duration, true) }}
    </span>

    <MdChip variant="flat" class="hidden md:inline-flex">{{ hint }}</MdChip>
  </div>
</template>
