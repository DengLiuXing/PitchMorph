<script setup lang="ts">
/**
 * 音高滑动窗口。
 *
 * 数据一次性取回（整首 f0），滑动时只在内存里切片，不再发请求。
 * 纵轴用对数映射：220–440 与 440–880 在听觉上等距，屏幕上也必须等距。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { PitchCurve } from '@/api/types'
import { hzToNote } from '@/lib/format'

const props = defineProps<{
  jobId: string
  position: number
}>()

const BOTTOM_HZ = 220 // A3
const OCTAVES = 2 // 顶部 880 Hz
const WINDOW_SEC = 5 // 播放头居中，左右各 2.5 s

// 实线是八度边界，虚线是人声换声区参考
const GRID: { hz: number; dashed: boolean }[] = [
  { hz: 220, dashed: false },
  { hz: 261.63, dashed: true },
  { hz: 440, dashed: false },
  { hz: 523.25, dashed: true },
  { hz: 880, dashed: false },
]

const canvas = ref<HTMLCanvasElement | null>(null)
const curve = ref<PitchCurve | null>(null)
const error = ref('')
const voicedRatio = ref(0)

function yOf(hz: number, height: number): number {
  const ratio = Math.log(hz / BOTTOM_HZ) / (Math.log(2) * OCTAVES)
  return height * (1 - ratio)
}

function draw(): void {
  const element = canvas.value
  const data = curve.value
  if (!element) return

  const ratio = window.devicePixelRatio || 1
  const width = element.clientWidth
  const height = element.clientHeight
  if (width === 0 || height === 0) return

  element.width = Math.round(width * ratio)
  element.height = Math.round(height * ratio)

  const painter = element.getContext('2d')
  if (!painter) return
  painter.setTransform(ratio, 0, 0, ratio, 0, 0)
  painter.clearRect(0, 0, width, height)

  // 网格
  painter.font = '10px "Cascadia Mono", ui-monospace, monospace'
  GRID.forEach(({ hz, dashed }) => {
    const y = Math.round(yOf(hz, height)) + 0.5
    if (y < 8 || y > height - 4) return
    painter.beginPath()
    painter.setLineDash(dashed ? [2, 4] : [])
    painter.strokeStyle = dashed ? 'rgba(139,147,161,0.14)' : 'rgba(139,147,161,0.26)'
    painter.lineWidth = 1
    painter.moveTo(0, y)
    painter.lineTo(width, y)
    painter.stroke()
    painter.setLineDash([])
    painter.fillStyle = 'rgba(92,100,114,0.85)'
    painter.fillText(`${hzToNote(hz)} · ${Math.round(hz)}`, 4, y - 3)
  })

  if (!data) return

  const hop = data.hop_sec
  const half = WINDOW_SEC / 2
  const from = Math.max(0, props.position - half)
  const span = WINDOW_SEC
  const first = Math.floor(from / hop)
  const last = Math.min(data.frames - 1, Math.ceil((from + span) / hop))
  const xOf = (frame: number): number => ((frame * hop - from) / span) * width

  // 竖条当填充：有声帧才画，静音段自然留白
  painter.fillStyle = 'rgba(240,169,22,0.12)'
  for (let frame = first; frame <= last; frame += 1) {
    const value = data.f0[frame]
    if (value === null) continue
    const x = xOf(frame)
    const y = yOf(value, height)
    painter.fillRect(x, y, Math.max(1.2, width / (span / hop)), height - y)
  }

  // 描边：None 处断开，不能 lineTo(0)，否则会画出一条贴着底边的假线
  painter.beginPath()
  painter.strokeStyle = '#F0A916'
  painter.lineWidth = 2
  painter.lineJoin = 'round'
  let pen = false
  for (let frame = first; frame <= last; frame += 1) {
    const value = data.f0[frame]
    if (value === null) {
      pen = false
      continue
    }
    const x = xOf(frame)
    const y = yOf(value, height)
    if (pen) painter.lineTo(x, y)
    else {
      painter.moveTo(x, y)
      pen = true
    }
  }
  painter.stroke()

  // 播放头固定在正中
  painter.strokeStyle = 'rgba(255,193,69,0.9)'
  painter.lineWidth = 1
  painter.beginPath()
  painter.moveTo(width / 2, 0)
  painter.lineTo(width / 2, height)
  painter.stroke()
}

async function load(): Promise<void> {
  curve.value = null
  error.value = ''
  try {
    const data = await api.pitch(props.jobId)
    curve.value = data
    const voiced = data.f0.filter((value) => value !== null).length
    voicedRatio.value = data.frames > 0 ? voiced / data.frames : 0
    draw()
  } catch (issue) {
    error.value = (issue as Error).message
  }
}

function onResize(): void {
  draw()
}

watch(() => props.jobId, () => void load())
watch(() => props.position, draw)

onMounted(() => {
  void load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>

<template>
  <div class="flex flex-col">
    <div class="flex items-center gap-3 px-6 py-2">
      <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">音高</span>
      <span class="num text-[11px] text-chalk-dim">
        {{ curve ? `${curve.frames} 帧 · hop ${curve.hop_sec}s` : '—' }}
      </span>
      <span v-if="curve" class="num text-[11px] text-teal">
        有声 {{ (voicedRatio * 100).toFixed(1) }}%
      </span>
      <span class="num text-[11px] text-chalk-mute">220–880 Hz · 窗口 {{ WINDOW_SEC }}s</span>
      <span v-if="error" class="text-[11px] text-red-400">{{ error }}</span>
    </div>

    <canvas ref="canvas" class="block h-[168px] w-full" />

    <p class="px-6 pb-2 pt-1 text-[10.5px] leading-relaxed text-chalk-mute">
      无声帧断开不连线。纵轴每格是一个八度，与听觉等距。
    </p>
  </div>
</template>
