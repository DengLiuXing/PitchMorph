<script setup lang="ts">
import { watch } from 'vue'

import type { Peaks } from '@/api/types'
import { useCanvas } from '@/composables/useCanvas'
import { drawWave } from '@/lib/canvas'

const props = defineProps<{
  peaks: Peaks | null
  duration: number
  pps: number
  scrollX: number
  viewWidth: number
  color: string
  /** 没有波形时的占位文案，默认「波形生成中」 */
  placeholder?: string
}>()

// 画布只覆盖视口（time offset = scrollX/pps），缩放与横滚都是重绘而不是拉大位图
const { canvas, paint } = useCanvas((ctx, size) =>
  drawWave(
    ctx,
    size,
    props.peaks,
    props.duration,
    props.pps,
    props.scrollX / props.pps,
    props.color,
    props.placeholder,
  ),
)

watch(
  () => [
    props.peaks,
    props.duration,
    props.pps,
    props.scrollX,
    props.viewWidth,
    props.color,
    props.placeholder,
  ],
  paint,
  { immediate: true },
)
</script>

<template>
  <canvas
    ref="canvas"
    class="lane-canvas"
    :style="{ left: `${scrollX}px`, width: `${Math.max(1, viewWidth)}px` }"
  />
</template>
