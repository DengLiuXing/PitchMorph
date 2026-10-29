<script setup lang="ts">
import { Download } from 'lucide-vue-next'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fetchPeaks } from '@/lib/wavPeaks'

const props = defineProps<{
  name: string
  label: string
  url: string
  downloadUrl: string
  downloadName: string
  progress: number
  volume: number
  muted: boolean
  soloed: boolean
  /** 被 solo 挤掉或被静音，整轨变暗 */
  dimmed: boolean
}>()

const emit = defineEmits<{
  (event: 'ready', payload: { name: string; element: HTMLAudioElement }): void
  (event: 'mute', name: string): void
  (event: 'solo', name: string): void
  // scrub 只更新预览位置，commit 才真正改 currentTime —— 否则拖动时每帧都发 Range 请求
  (event: 'scrub', ratio: number): void
  (event: 'commit'): void
  (event: 'volume', payload: { name: string; value: number }): void
}>()

const BUCKETS = 900

const audio = ref<HTMLAudioElement | null>(null)
const base = ref<HTMLCanvasElement | null>(null)
const lit = ref<HTMLCanvasElement | null>(null)
const peakLabel = ref('—')

let dragging = false

function paint(canvas: HTMLCanvasElement | null, peaks: Float32Array, colour: string): void {
  if (!canvas) return
  const ratio = window.devicePixelRatio || 1
  const width = canvas.clientWidth
  const height = canvas.clientHeight
  if (width === 0 || height === 0) return

  canvas.width = Math.round(width * ratio)
  canvas.height = Math.round(height * ratio)

  const painter = canvas.getContext('2d')
  if (!painter) return
  painter.setTransform(ratio, 0, 0, ratio, 0, 0)
  painter.clearRect(0, 0, width, height)

  painter.fillStyle = colour
  const middle = height / 2
  const step = width / peaks.length

  for (let index = 0; index < peaks.length; index += 1) {
    const amplitude = Math.max(0.5, peaks[index] * middle * 0.94)
    painter.fillRect(index * step, middle - amplitude, Math.max(step, 0.7), amplitude * 2)
  }
}

async function draw(): Promise<void> {
  const peaks = await fetchPeaks(props.url, BUCKETS)
  if (!peaks) return
  let peak = 0
  for (const value of peaks) peak = Math.max(peak, value)
  peakLabel.value = (20 * Math.log10(Math.max(peak, 1e-6))).toFixed(1)

  paint(base.value, peaks, 'rgba(138,148,166,0.42)')
  paint(lit.value, peaks, '#F2A93B')
}

function ratioAt(event: PointerEvent): number {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width === 0) return 0
  return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
}

function onPointerDown(event: PointerEvent): void {
  const element = event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  dragging = true
  emit('scrub', ratioAt(event))
}

function onPointerMove(event: PointerEvent): void {
  if (!dragging) return
  emit('scrub', ratioAt(event))
}

function onPointerUp(event: PointerEvent): void {
  if (!dragging) return
  dragging = false
  const element = event.currentTarget as HTMLElement
  if (element.hasPointerCapture(event.pointerId)) {
    element.releasePointerCapture(event.pointerId)
  }
  emit('scrub', ratioAt(event))
  emit('commit')
}

function onVolume(event: Event): void {
  const value = Number((event.target as HTMLInputElement).value) / 100
  emit('volume', { name: props.name, value })
}

function handleResize(): void {
  void draw()
}

onMounted(() => {
  if (audio.value) emit('ready', { name: props.name, element: audio.value })
  void draw()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))

watch(() => props.url, () => void draw())
</script>

<template>
  <article class="track" :data-dimmed="dimmed ? 'true' : 'false'">
    <div class="track-head">
      <span class="track-name" :title="`${label} · 峰值 ${peakLabel} dB`">{{ label }}</span>

      <div class="track-ms">
        <button
          type="button"
          class="ms"
          data-act="mute"
          :aria-pressed="muted"
          title="静音"
          @click="emit('mute', name)"
        >
          M
        </button>
        <button
          type="button"
          class="ms"
          data-act="solo"
          :aria-pressed="soloed"
          title="独奏"
          @click="emit('solo', name)"
        >
          S
        </button>
      </div>

      <input
        class="track-vol"
        type="range"
        min="0"
        max="100"
        :value="Math.round(volume * 100)"
        :aria-label="`${label} 音量`"
        @input="onVolume"
      />

      <a class="track-dl" :href="downloadUrl" :download="downloadName" :title="`下载 ${downloadName}`">
        <Download :size="13" :stroke-width="1.7" />
      </a>
    </div>

    <div
      class="wave cursor-crosshair touch-none select-none"
      :style="{ '--p': `${progress * 100}%` }"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    >
      <canvas ref="base" class="wave-base" />
      <canvas ref="lit" class="wave-lit" />
    </div>

    <audio ref="audio" :src="url" preload="auto" crossorigin="anonymous" class="hidden" />
  </article>
</template>
