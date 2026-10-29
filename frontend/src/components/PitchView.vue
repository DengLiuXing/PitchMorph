<script setup lang="ts">
/**
 * 音高滑动窗口 + 参数调优。
 *
 * 数据一次性取回（整首 f0），滑动时只在内存里切片，不再发请求。
 * 纵轴用对数映射：220–440 与 440–880 在听觉上等距，屏幕上也必须等距。
 *
 * 断线多的时候不用猜阈值：改参数重跑一遍，上一版留成灰色虚线叠在上面，
 * 两次结果的差别一眼能看出来。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { PitchCurve } from '@/api/types'
import { isActive } from '@/api/types'
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

const SMOOTH_OPTIONS = [0, 3, 5, 9]

// 音域收窄能减少倍频误判，但超出范围的真音也会丢 —— 这是要用户自己权衡的
const RANGES = [
  { id: 'full', label: '全音域', min: 80, max: 1100 },
  { id: 'male', label: '男声', min: 75, max: 500 },
  { id: 'female', label: '女声', min: 160, max: 1100 },
]

// 阈值是 FCPE 的浊音置信度门槛：低一点检出更多，但气声也会被当成音
const THRESHOLDS = [
  { id: 'loose', label: '灵敏', value: 0.002 },
  { id: 'normal', label: '标准', value: 0.006 },
  { id: 'strict', label: '严格', value: 0.02 },
]

// 补洞只补「两侧都有音高」且音高差 < 2.5 半音的空洞。
// 档位给到 400 ms 是因为实测 80 ms 根本盖不住大部分断线 —— 那多半是清辅音与弱起。
const GAPS = [
  { id: 0, label: '关' },
  { id: 40, label: '40' },
  { id: 80, label: '80' },
  { id: 150, label: '150' },
  { id: 250, label: '250' },
  { id: 400, label: '400' },
]

const canvas = ref<HTMLCanvasElement | null>(null)
const curve = ref<PitchCurve | null>(null)
/** 上一次的参数与曲线，叠成虚线做对比 */
const previous = ref<PitchCurve | null>(null)
const error = ref('')
const voicedRatio = ref(0)
const smoothWindow = ref(3)
const busy = ref(false)

const rangeId = ref('full')
const thresholdId = ref('normal')
const gapMs = ref(80)

const display = computed(() => {
  const data = curve.value
  if (!data) return null
  return medianFilter(data.f0, smoothWindow.value)
})

const previousDisplay = computed(() => {
  const data = previous.value
  if (!data) return null
  return medianFilter(data.f0, smoothWindow.value)
})

/**
 * 中值滤波：压掉密集的小尖峰。
 *
 * 关键一点是 None 帧保持 None —— 换气位置不能被平滑填上，
 * 否则静音段会长出一条假曲线，音符边界也会跟着漂。
 */
function medianFilter(values: (number | null)[], window: number): (number | null)[] {
  if (window <= 1) return values
  const half = Math.floor(window / 2)
  return values.map((value, index) => {
    if (value === null) return null
    const neighbours: number[] = []
    for (let offset = -half; offset <= half; offset += 1) {
      const sample = values[index + offset]
      if (sample !== undefined && sample !== null) neighbours.push(sample)
    }
    if (neighbours.length === 0) return null
    neighbours.sort((a, b) => a - b)
    return neighbours[Math.floor(neighbours.length / 2)]
  })
}

function yOf(hz: number, height: number): number {
  const ratio = Math.log(hz / BOTTOM_HZ) / (Math.log(2) * OCTAVES)
  return height * (1 - ratio)
}

interface Slice {
  first: number
  last: number
  hop: number
  from: number
  span: number
  width: number
  height: number
}

function stroke(
  painter: CanvasRenderingContext2D,
  values: (number | null)[],
  box: Slice,
  colour: string,
  dashed: boolean,
): void {
  const xOf = (frame: number): number => ((frame * box.hop - box.from) / box.span) * box.width
  painter.beginPath()
  painter.strokeStyle = colour
  painter.lineWidth = dashed ? 1.2 : 2
  painter.lineJoin = 'round'
  painter.setLineDash(dashed ? [3, 4] : [])
  let pen = false
  for (let frame = box.first; frame <= box.last; frame += 1) {
    const value = values[frame]
    if (value === null || value === undefined) {
      pen = false
      continue
    }
    const x = xOf(frame)
    const y = yOf(value, box.height)
    if (pen) painter.lineTo(x, y)
    else {
      painter.moveTo(x, y)
      pen = true
    }
  }
  painter.stroke()
  painter.setLineDash([])
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

  painter.font = '10px "Cascadia Mono", ui-monospace, monospace'
  GRID.forEach(({ hz, dashed }) => {
    const y = Math.round(yOf(hz, height)) + 0.5
    if (y < 8 || y > height - 4) return
    painter.beginPath()
    painter.setLineDash(dashed ? [2, 4] : [])
    painter.strokeStyle = dashed ? 'rgba(138,148,166,0.14)' : 'rgba(138,148,166,0.26)'
    painter.lineWidth = 1
    painter.moveTo(0, y)
    painter.lineTo(width, y)
    painter.stroke()
    painter.setLineDash([])
    painter.fillStyle = 'rgba(90,99,115,0.85)'
    painter.fillText(`${hzToNote(hz)} · ${Math.round(hz)}`, 4, y - 3)
  })

  const values = display.value
  if (!data || !values) return

  const half = WINDOW_SEC / 2
  const box: Slice = {
    hop: data.hop_sec,
    from: Math.max(0, props.position - half),
    span: WINDOW_SEC,
    width,
    height,
    first: 0,
    last: 0,
  }
  box.first = Math.floor(box.from / box.hop)
  box.last = Math.min(data.frames - 1, Math.ceil((box.from + box.span) / box.hop))

  // 上一版先用灰虚线画在底层，再画当前版，重叠时能看出差在哪
  const before = previousDisplay.value
  if (before) stroke(painter, before, box, 'rgba(138,148,166,0.55)', true)

  // 竖条当填充：有声帧才画，静音段自然留白
  const xOf = (frame: number): number => ((frame * box.hop - box.from) / box.span) * width
  painter.fillStyle = 'rgba(242,169,59,0.12)'
  for (let frame = box.first; frame <= box.last; frame += 1) {
    const value = values[frame]
    if (value === null || value === undefined) continue
    const x = xOf(frame)
    const y = yOf(value, height)
    painter.fillRect(x, y, Math.max(1.2, width / (box.span / box.hop)), height - y)
  }

  // 描边：None 处断开，不能 lineTo(0)，否则会画出一条贴着底边的假线
  stroke(painter, values, box, '#F2A93B', false)

  // 播放头固定在正中
  painter.strokeStyle = 'rgba(255,196,107,0.9)'
  painter.lineWidth = 1
  painter.beginPath()
  painter.moveTo(width / 2, 0)
  painter.lineTo(width / 2, height)
  painter.stroke()
}

function applyParams(data: PitchCurve): void {
  const params = data.params
  if (!params) return
  const matched = RANGES.find(
    (item) => item.min === params.f0_min && item.max === params.f0_max,
  )
  rangeId.value = matched?.id ?? 'full'
  const threshold = THRESHOLDS.find((item) => Math.abs(item.value - params.threshold) < 1e-6)
  thresholdId.value = threshold?.id ?? 'normal'
  gapMs.value = params.gap_ms
}

async function load(): Promise<void> {
  error.value = ''
  try {
    const data = await api.pitch(props.jobId)
    curve.value = data
    applyParams(data)
    const voiced = data.f0.filter((value) => value !== null).length
    voicedRatio.value = data.frames > 0 ? voiced / data.frames : 0
    draw()
  } catch (issue) {
    error.value = (issue as Error).message
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitDone(): Promise<void> {
  // 一轮音高只要几秒；上限 2 分钟，超时就把错误抛给用户
  for (let tick = 0; tick < 240; tick += 1) {
    await sleep(500)
    const list = await api.jobs()
    const job = list.find((item) => item.id === props.jobId)
    if (!job || !isActive(job.stage)) return
  }
  throw new Error('重跑超时，请检查服务端日志')
}

async function rerun(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const before = curve.value
  try {
    const range = RANGES.find((item) => item.id === rangeId.value) ?? RANGES[0]
    const threshold = THRESHOLDS.find((item) => item.id === thresholdId.value) ?? THRESHOLDS[1]
    await api.rerunPitch(props.jobId, {
      f0_min: range.min,
      f0_max: range.max,
      threshold: threshold.value,
      gap_ms: gapMs.value,
    })
    await waitDone()
    previous.value = before
    await load()
  } catch (issue) {
    error.value = (issue as Error).message
  } finally {
    busy.value = false
  }
}

function onResize(): void {
  draw()
}

watch(() => props.jobId, () => {
  previous.value = null
  curve.value = null
  void load()
})
watch(() => props.position, draw)
watch(smoothWindow, draw)

onMounted(() => {
  void load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="flex flex-wrap items-center gap-3 px-6 py-2">
      <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">音高</span>
      <span class="chip border-amber-line text-amber-bright" title="FCPE 单声部音高 + 启发式切分，断线与音符边界都不保证准确">
        实验性
      </span>
      <span class="num text-[11px] text-chalk-dim">
        {{ curve ? `${curve.frames} 帧 · hop ${curve.hop_sec}s` : '—' }}
      </span>
      <span v-if="curve" class="num text-[11px] text-teal">
        有声 {{ (voicedRatio * 100).toFixed(1) }}%
      </span>
      <span v-if="error" class="text-[11px] text-red-400">{{ error }}</span>

      <div class="ml-auto flex items-center gap-1">
        <span class="text-[10.5px] text-chalk-mute">平滑</span>
        <button
          v-for="option in SMOOTH_OPTIONS"
          :key="option"
          type="button"
          class="cursor-pointer rounded px-1.5 py-0.5 text-[10.5px] transition-colors duration-150"
          :class="
            smoothWindow === option
              ? 'bg-amber/20 text-amber-bright'
              : 'text-chalk-mute hover:text-chalk'
          "
          :title="option === 0 ? '显示原始曲线' : `中值滤波 ${option} 帧`"
          @click="smoothWindow = option"
        >
          {{ option === 0 ? '关' : `${option} 帧` }}
        </button>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-6 pb-2">
      <span class="text-[10.5px] text-chalk-mute">音域</span>
      <div class="flex overflow-hidden rounded border border-white/[0.08]">
        <button
          v-for="item in RANGES"
          :key="item.id"
          type="button"
          class="cursor-pointer px-2 py-0.5 text-[10.5px] transition-colors duration-150"
          :class="
            rangeId === item.id
              ? 'bg-amber/15 text-amber-bright'
              : 'text-chalk-mute hover:text-chalk'
          "
          :title="`${item.min}–${item.max} Hz`"
          @click="rangeId = item.id"
        >
          {{ item.label }}
        </button>
      </div>

      <span class="text-[10.5px] text-chalk-mute">阈值</span>
      <div class="flex overflow-hidden rounded border border-white/[0.08]">
        <button
          v-for="item in THRESHOLDS"
          :key="item.id"
          type="button"
          class="cursor-pointer px-2 py-0.5 text-[10.5px] transition-colors duration-150"
          :class="
            thresholdId === item.id
              ? 'bg-amber/15 text-amber-bright'
              : 'text-chalk-mute hover:text-chalk'
          "
          :title="`浊音置信度门槛 ${item.value}`"
          @click="thresholdId = item.id"
        >
          {{ item.label }}
        </button>
      </div>

      <span class="text-[10.5px] text-chalk-mute">补洞</span>
      <div class="flex overflow-hidden rounded border border-white/[0.08]">
        <button
          v-for="item in GAPS"
          :key="item.id"
          type="button"
          class="cursor-pointer px-2 py-0.5 text-[10.5px] transition-colors duration-150"
          :class="
            gapMs === item.id
              ? 'bg-amber/15 text-amber-bright'
              : 'text-chalk-mute hover:text-chalk'
          "
          :title="item.id === 0 ? '不补洞' : `补 ${item.id} ms 以内的断线`"
          @click="gapMs = item.id"
        >
          {{ item.label }}
        </button>
      </div>

      <button
        type="button"
        class="btn ml-auto !px-2.5 !py-0.5 !text-[10.5px]"
        :disabled="busy"
        :class="{ 'opacity-50': busy }"
        title="用当前参数重跑音高，不重新分离"
        @click="rerun"
      >
        {{ busy ? '重跑中…' : '重新提取' }}
      </button>

      <span v-if="previous" class="flex items-center gap-1 text-[10px] text-chalk-mute">
        <span class="inline-block h-0 w-4 border-t border-dashed border-chalk-dim" />
        上一版
      </span>
    </div>

    <canvas ref="canvas" class="block min-h-0 w-full flex-1" />
  </div>
</template>
