<script setup lang="ts">
import { watch } from 'vue'

import { useCanvas } from '@/composables/useCanvas'
import { drawRuler } from '@/lib/canvas'
import { formatTime } from '@/lib/format'

const props = defineProps<{ pps: number; scrollX: number; viewWidth: number }>()

const { canvas, paint } = useCanvas((ctx, size) =>
  drawRuler(ctx, size, props.pps, props.scrollX / props.pps, (seconds) => formatTime(seconds)),
)

watch(() => [props.pps, props.scrollX, props.viewWidth], paint, { immediate: true })
</script>

<template>
  <canvas
    ref="canvas"
    class="lane-canvas"
    :style="{ left: `${scrollX}px`, width: `${Math.max(1, viewWidth)}px` }"
  />
</template>
