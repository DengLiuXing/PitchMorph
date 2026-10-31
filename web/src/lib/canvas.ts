import type { Peaks, PitchCurve } from '@/api/types'
import { hzToMidi } from '@/lib/format'
import { canvasPalette } from '@/lib/md3'

/** 刻度步长候选（秒）。取第一个在屏幕上够宽的，避免标签糊成一片 */
const TICK_STEPS = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]

export interface Size {
  width: number
  height: number
}

/**
 * 把 canvas 的位图尺寸对齐到 CSS 尺寸 × DPR，返回逻辑尺寸的 2D 上下文。
 * 每帧都调用：窗口/容器变化时不需要额外记账。
 */
export function prepare(canvas: HTMLCanvasElement): { ctx: CanvasRenderingContext2D; size: Size } | null {
  const width = canvas.clientWidth
  const height = canvas.clientHeight
  if (width === 0 || height === 0) return null
  const dpr = window.devicePixelRatio || 1
  const bitmapWidth = Math.round(width * dpr)
  const bitmapHeight = Math.round(height * dpr)
  if (canvas.width !== bitmapWidth || canvas.height !== bitmapHeight) {
    canvas.width = bitmapWidth
    canvas.height = bitmapHeight
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  return { ctx, size: { width, height } }
}

export function tickStep(pps: number): number {
  const found = TICK_STEPS.find((step) => step * pps >= 72)
  return found ?? TICK_STEPS[TICK_STEPS.length - 1]
}

/** 时间标尺：主刻度带 mm:ss，次刻度只画短线 */
export function drawRuler(
  ctx: CanvasRenderingContext2D,
  size: Size,
  pps: number,
  offset: number,
  label: (seconds: number) => string,
): void {
  const palette = canvasPalette()
  const { width, height } = size
  const step = tickStep(pps)
  const first = Math.floor(offset / step) * step
  const last = offset + width / pps

  ctx.font = palette.fontLarge
  ctx.textBaseline = 'alphabetic'

  for (let seconds = first; seconds <= last + step; seconds += step) {
    const x = Math.round((seconds - offset) * pps) + 0.5
    if (x < -1 || x > width + 1) continue
    ctx.strokeStyle = palette.grid
    ctx.beginPath()
    ctx.moveTo(x, height - 9)
    ctx.lineTo(x, height)
    ctx.stroke()

    ctx.fillStyle = palette.label
    ctx.fillText(label(Math.max(0, seconds)), x + 4, height - 12)

    // 五等分次刻度
    const minor = step / 5
    for (let index = 1; index < 5; index += 1) {
      const subX = Math.round((seconds + minor * index - offset) * pps) + 0.5
      if (subX < 0 || subX > width) continue
      ctx.strokeStyle = palette.gridMinor
      ctx.beginPath()
      ctx.moveTo(subX, height - 4)
      ctx.lineTo(subX, height)
      ctx.stroke()
    }
  }
}

/** 音频结束后的空白压暗，一眼能看出歌有多长、后面没有内容 */
function shadeAfter(
  ctx: CanvasRenderingContext2D,
  size: Size,
  pps: number,
  offset: number,
  seconds: number,
): void {
  const end = (seconds - offset) * pps
  if (end >= size.width) return
  const from = Math.max(0, end)
  ctx.fillStyle = canvasPalette().shade
  ctx.fillRect(from, 0, size.width - from, size.height)
}

/** 与标尺同栅格的竖线：轨道和刻度共用一套步长，对齐才看得出来 */
function drawGrid(ctx: CanvasRenderingContext2D, size: Size, pps: number, offset: number): void {
  const palette = canvasPalette()
  const step = tickStep(pps)
  const first = Math.floor(offset / step) * step
  ctx.strokeStyle = palette.gridMinor
  for (let seconds = first; seconds <= offset + size.width / pps; seconds += step) {
    const x = Math.round((seconds - offset) * pps) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, size.height)
    ctx.stroke()
  }
}

/**
 * 波形：只按当前视口（offset 秒起）采样，所以 canvas 宽度永远等于容器宽，
 * 缩放/横滚时重绘而不是拉一张几万像素的位图。
 */
export function drawWave(
  ctx: CanvasRenderingContext2D,
  size: Size,
  peaks: Peaks | null,
  duration: number,
  pps: number,
  offset: number,
  color: string,
  placeholder?: string,
): void {
  const palette = canvasPalette()
  const { width, height } = size
  const middle = height / 2

  drawGrid(ctx, size, pps, offset)

  ctx.strokeStyle = palette.axis
  ctx.beginPath()
  ctx.moveTo(0, Math.round(middle) + 0.5)
  ctx.lineTo(width, Math.round(middle) + 0.5)
  ctx.stroke()

  const buckets = peaks?.max.length ?? 0
  if (!peaks || buckets === 0 || duration <= 0) {
    ctx.fillStyle = palette.labelDim
    ctx.font = palette.fontLarge
    ctx.fillText(placeholder ?? '波形生成中', 12, middle + 4)
    shadeAfter(ctx, size, pps, offset, duration)
    return
  }

  const secondsPerBucket = duration / buckets
  const half = middle * 0.9

  ctx.fillStyle = color
  for (let x = 0; x < width; x += 1) {
    const from = Math.floor((offset + x / pps) / secondsPerBucket)
    const to = Math.max(from + 1, Math.floor((offset + (x + 1) / pps) / secondsPerBucket))
    let low = 0
    let high = 0
    for (let index = from; index < to; index += 1) {
      if (index < 0 || index >= buckets) continue
      low = Math.min(low, peaks.min[index])
      high = Math.max(high, peaks.max[index])
    }
    const top = middle - high * half
    const bottom = middle - low * half
    ctx.fillRect(x, top, 1, Math.max(1, bottom - top))
  }

  shadeAfter(ctx, size, pps, offset, duration)
}

export interface MidiRange {
  low: number
  high: number
}

/**
 * 从曲线本身定纵轴范围（半音取整，留半音边距）。
 * 用 2% 分位剔除误检帧（F#1 那种），只有裁掉超过一个八度才认作离群——
 * 真的唱得很宽时不会被压扁。
 */
export function pitchRange(curve: PitchCurve | null): MidiRange {
  const fallback: MidiRange = { low: 45, high: 76 }
  if (!curve) return fallback

  const values: number[] = []
  for (let index = 0; index < curve.f0.length; index += 1) {
    if (!curve.voiced[index] || curve.f0[index] <= 0) continue
    const midi = hzToMidi(curve.f0[index])
    if (Number.isFinite(midi)) values.push(midi)
  }
  if (values.length === 0) return fallback

  values.sort((left, right) => left - right)
  const low = values[0]
  const high = values[values.length - 1]
  const lowPercentile = values[Math.floor((values.length - 1) * 0.02)]
  const highPercentile = values[Math.ceil((values.length - 1) * 0.98)]
  const bottom = lowPercentile - low > 12 ? lowPercentile : low
  const top = high - highPercentile > 12 ? highPercentile : high

  const pad = Math.max(2, (top - bottom) * 0.15)
  return { low: Math.floor(bottom - pad), high: Math.ceil(top + pad) }
}

export interface PitchWindow {
  /** 窗口起止（秒），由调用方保证 to > from */
  from: number
  to: number
  /** 当前播放位置，画中心光标与两端相对秒数 */
  cursor: number
}

/**
 * 音高曲线：固定时长（默认 5 s）的滑动窗口，横向**铺满整块画布**。
 *
 * 与时间轴的缩放解耦——播放头不动，曲线从右往左滑过去，
 * 所以这里不用 pps，只认窗口的起止与画布宽度。
 */
export function drawPitch(
  ctx: CanvasRenderingContext2D,
  size: Size,
  curve: PitchCurve,
  range: MidiRange,
  color: string,
  win: PitchWindow,
): void {
  const palette = canvasPalette()
  const { width, height } = size
  const seconds = Math.max(0.001, win.to - win.from)
  const xFor = (time: number) => ((time - win.from) / seconds) * width
  const span = Math.max(1, range.high - range.low)
  const yFor = (midi: number) => height - ((midi - range.low) / span) * height

  // 每秒一条竖线，扫视时能数清时间
  ctx.strokeStyle = palette.gridMinor
  ctx.lineWidth = 1
  for (let second = Math.ceil(win.from); second <= win.to; second += 1) {
    const x = Math.round(xFor(second)) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
  }

  ctx.font = palette.fontSmall
  ctx.textAlign = 'left'
  const gridStep = span > 24 ? 12 : span > 12 ? 6 : 3
  for (let midi = Math.ceil(range.low / gridStep) * gridStep; midi <= range.high; midi += gridStep) {
    const y = Math.round(yFor(midi)) + 0.5
    ctx.strokeStyle = palette.grid
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
    ctx.fillStyle = palette.labelDim
    ctx.fillText(`C${Math.floor(midi / 12) - 1}`, 3, y - 3)
  }

  drawPitchCurve(ctx, size, curve, range, color, win)

  // 中心光标 = 当前播放位置，窗口两端标相对秒
  const cursorX = Math.round(xFor(Math.min(Math.max(win.cursor, win.from), win.to))) + 0.5
  ctx.strokeStyle = palette.cursor
  ctx.beginPath()
  ctx.moveTo(cursorX, 0)
  ctx.lineTo(cursorX, height)
  ctx.stroke()

  ctx.fillStyle = palette.labelDim
  ctx.fillText(`${Math.round(win.from - win.cursor)}s`, 4, height - 4)
  ctx.textAlign = 'right'
  ctx.fillText(`+${Math.round(win.to - win.cursor)}s`, width - 4, height - 4)
  ctx.textAlign = 'left'
}

/** 只画曲线本体（半透明色块 + 折线），网格与光标由调用方负责。 */
function drawPitchCurve(
  ctx: CanvasRenderingContext2D,
  size: Size,
  curve: PitchCurve,
  range: MidiRange,
  color: string,
  win: PitchWindow,
): void {
  const palette = canvasPalette()
  const { width, height } = size
  const seconds = Math.max(0.001, win.to - win.from)
  const xFor = (time: number) => ((time - win.from) / seconds) * width
  const span = Math.max(1, range.high - range.low)
  const yFor = (midi: number) => height - ((midi - range.low) / span) * height

  // 二分定位窗口起点：15 分钟是 9 万帧，逐帧扫太浪费
  let low = 0
  let high = Math.max(0, curve.times.length - 1)
  while (low < high) {
    const mid = (low + high) >> 1
    if (curve.times[mid] < win.from) low = mid + 1
    else high = mid
  }

  const voicedAt = (index: number): boolean => curve.voiced[index] && curve.f0[index] > 0

  // 曲线下方的半透明色块：比一根细线更容易看出音高走向，
  // 有声段之间本来就该断开，不补假数据
  ctx.beginPath()
  let open = false
  let lastX = 0
  for (let index = low; index < curve.times.length; index += 1) {
    const time = curve.times[index]
    if (time > win.to) break
    if (!voicedAt(index)) {
      if (open) {
        ctx.lineTo(lastX, height)
        ctx.closePath()
        open = false
      }
      continue
    }
    const x = xFor(time)
    const y = yFor(hzToMidi(curve.f0[index]))
    if (open) {
      ctx.lineTo(x, y)
    } else {
      ctx.moveTo(x, height)
      ctx.lineTo(x, y)
      open = true
    }
    lastX = x
  }
  if (open) {
    ctx.lineTo(lastX, height)
    ctx.closePath()
  }
  ctx.globalAlpha = 0.14
  ctx.fillStyle = color
  ctx.fill()
  ctx.globalAlpha = 1

  ctx.strokeStyle = color
  ctx.lineWidth = 2
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  ctx.beginPath()

  let drawing = false
  for (let index = low; index < curve.times.length; index += 1) {
    const time = curve.times[index]
    if (time > win.to) break
    if (!voicedAt(index)) {
      drawing = false
      continue
    }
    const x = xFor(time)
    const y = yFor(hzToMidi(curve.f0[index]))
    if (drawing) ctx.lineTo(x, y)
    else {
      ctx.moveTo(x, y)
      drawing = true
    }
  }
  ctx.stroke()
}

/** 全屏钢琴卷帘的固定音域：C3 到 C6 */
export const ROLL_RANGE: MidiRange = { low: 48, high: 84 }

export interface RollNote {
  start_sec: number
  dur_sec: number
  midi: number
}

/**
 * 全屏钢琴卷帘：固定 C3-C6 音域，OpenUtau 风格的音符块 + 音高曲线 + 播放头。
 * 网格每半音一条细线，C 音行加粗并标音名。
 */
export function drawPianoRoll(
  ctx: CanvasRenderingContext2D,
  size: Size,
  curve: PitchCurve,
  color: string,
  win: PitchWindow,
  notes: RollNote[],
): void {
  const palette = canvasPalette()
  const range = ROLL_RANGE
  const { width, height } = size
  const seconds = Math.max(0.001, win.to - win.from)
  const xFor = (time: number) => ((time - win.from) / seconds) * width
  const span = Math.max(1, range.high - range.low)
  const rowH = height / span
  const yCenter = (midi: number) => height - ((midi - range.low + 0.5) / span) * height

  // 背景按十二平均律上色：1234567（自然音级）亮浅橙，半音（黑键）行浅褐；
  // 整层贴着背景画，网格、音符、曲线都压在它上面
  const blackKeys = new Set([1, 3, 6, 8, 10])
  for (let midi = range.low; midi <= range.high; midi += 1) {
    const yTop = height - ((midi - range.low + 1) / span) * height
    ctx.fillStyle = blackKeys.has(((midi % 12) + 12) % 12)
      ? 'rgba(141, 110, 76, 0.20)' // 浅褐
      : 'rgba(255, 183, 77, 0.16)' // 亮浅橙
    ctx.fillRect(0, yTop, width, rowH + 0.5)
  }

  // 半音网格：行分界线加粗提亮，C 行最粗并标音名
  ctx.font = palette.fontSmall
  ctx.textAlign = 'left'
  for (let midi = range.low; midi <= range.high; midi += 1) {
    const y = Math.round(yCenter(midi) + rowH / 2) + 0.5
    const isC = midi % 12 === 0
    ctx.strokeStyle = isC ? palette.cursor : palette.axis
    ctx.lineWidth = isC ? 2.5 : 1.5
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
    if (isC) {
      ctx.fillStyle = palette.labelDim
      ctx.fillText(`C${Math.floor(midi / 12) - 1}`, 3, y - 3)
    }
  }

  // 音符块：与窗口相交的都画，OpenUtau 风格的圆角横条
  const blockH = Math.max(3, rowH * 0.7)
  for (const note of notes) {
    const end = note.start_sec + note.dur_sec
    if (end <= win.from || note.start_sec >= win.to) continue
    if (note.midi < range.low || note.midi > range.high) continue
    const x = xFor(note.start_sec)
    const w = Math.max(3, xFor(end) - x)
    const y = yCenter(note.midi) - blockH / 2
    ctx.beginPath()
    ctx.roundRect(x + 0.5, y + 0.5, Math.max(1, w - 1), blockH, 3)
    ctx.fillStyle = color
    ctx.globalAlpha = 0.55
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.stroke()
  }

  drawPitchCurve(ctx, size, curve, range, color, win)

  // 中心光标
  const cursorX = Math.round(xFor(Math.min(Math.max(win.cursor, win.from), win.to))) + 0.5
  ctx.strokeStyle = palette.cursor
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(cursorX, 0)
  ctx.lineTo(cursorX, height)
  ctx.stroke()
}
