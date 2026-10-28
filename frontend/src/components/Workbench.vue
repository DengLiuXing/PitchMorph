<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { baseName, fmtTime } from '@/api/format'
import { STEM_LABELS, STEM_ORDER, type Job } from '@/api/types'

const props = defineProps<{ job: Job }>()
defineEmits<{ (event: 'back'): void }>()

interface TrackView {
  name: string
  url: string
  muted: boolean
  solo: boolean
  volume: number
  peaks: Float32Array | null
  ready: boolean
}

interface PeakColors {
  edge: string
  core: string
  flat: string
}

const BASE_COLORS: PeakColors = {
  edge: 'rgba(138, 148, 166, 0.30)',
  core: 'rgba(138, 148, 166, 0.52)',
  flat: 'rgba(138, 148, 166, 0.22)',
}

const LIT_COLORS: PeakColors = {
  edge: 'rgba(242, 169, 59, 0.55)',
  core: 'rgba(255, 196, 107, 0.98)',
  flat: 'rgba(242, 169, 59, 0.4)',
}

const tracks = ref<TrackView[]>([])
const playing = ref(false)
const loop = ref(false)
const duration = ref(0)
const position = ref(0)
const dragging = ref(false)

const tracksEl = ref<HTMLElement | null>(null)
const seekEl = ref<HTMLElement | null>(null)
const headOffset = ref(0)
const waveWidth = ref(0)

const canvases = new Map<string, { base?: HTMLCanvasElement; lit?: HTMLCanvasElement }>()
const audios = new Map<string, HTMLAudioElement>()
const gains = new Map<string, GainNode>()

let ctx: AudioContext | null = null
let raf = 0

const zipUrl = computed(() => `/api/jobs/${props.job.id}/download`)
const ratio = computed(() => (duration.value ? Math.min(1, position.value / duration.value) : 0))
const progressPct = computed(() => `${(ratio.value * 100).toFixed(3)}%`)
const playheadX = computed(() => `${headOffset.value + ratio.value * waveWidth.value}px`)
const currentTime = computed(() => fmtTime(position.value))
const totalTime = computed(() => fmtTime(duration.value))
const playheadVisible = computed(() => ratio.value > 0 || playing.value)

function label(name: string): string {
  return STEM_LABELS[name] ?? name
}

function downloadName(name: string): string {
  return `${baseName(props.job.filename)}-${name}.wav`
}

function registerCanvas(name: string, kind: 'base' | 'lit', element: unknown): void {
  if (!(element instanceof HTMLCanvasElement)) return
  const entry = canvases.get(name) ?? {}
  entry[kind] = element
  canvases.set(name, entry)
}

/* ── waveform ─────────────────────────────────────────────── */

function paint(canvas: HTMLCanvasElement | undefined, peaks: Float32Array | null, colors: PeakColors): void {
  if (!canvas) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const width = canvas.clientWidth || 1
  const height = canvas.clientHeight || 1
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(height * dpr)

  const painter = canvas.getContext('2d')
  if (!painter) return
  painter.setTransform(dpr, 0, 0, dpr, 0, 0)
  painter.clearRect(0, 0, width, height)

  if (!peaks) {
    painter.fillStyle = colors.flat
    painter.fillRect(0, height / 2 - 1, width, 2)
    return
  }

  const gradient = painter.createLinearGradient(0, 0, 0, height)
  gradient.addColorStop(0, colors.edge)
  gradient.addColorStop(0.5, colors.core)
  gradient.addColorStop(1, colors.edge)
  painter.fillStyle = gradient

  const bar = 2
  const gap = 1
  const middle = height / 2
  const maxAmplitude = height * 0.46

  for (let x = 0; x < width; x += bar + gap) {
    const index = Math.min(peaks.length - 1, Math.floor((x / width) * peaks.length))
    const amplitude = Math.max(0.012, peaks[index]) * maxAmplitude
    painter.beginPath()
    painter.roundRect(x, middle - amplitude, bar, amplitude * 2, 1)
    painter.fill()
  }
}

function repaint(name: string): void {
  const track = tracks.value.find((item) => item.name === name)
  const entry = canvases.get(name)
  paint(entry?.base, track?.peaks ?? null, BASE_COLORS)
  paint(entry?.lit, track?.peaks ?? null, LIT_COLORS)
}

/** Parse a PCM-16 RIFF/WAV buffer into normalised peak buckets. */
function wavPeaks(buffer: ArrayBuffer, buckets: number): Float32Array | null {
  const view = new DataView(buffer)
  if (buffer.byteLength < 44 || view.getUint32(0, false) !== 0x52494646) return null

  let offset = 12
  let channels = 0
  let bits = 0
  let dataStart = 0
  let dataLength = 0

  while (offset + 8 <= buffer.byteLength) {
    const id = view.getUint32(offset, false)
    const size = view.getUint32(offset + 4, true)
    const body = offset + 8
    if (id === 0x666d7420) {
      channels = view.getUint16(body + 2, true)
      bits = view.getUint16(body + 14, true)
    } else if (id === 0x64617461) {
      dataStart = body
      dataLength = Math.min(size, buffer.byteLength - body)
      break
    }
    offset = body + size + (size % 2)
  }

  if (!channels || bits !== 16 || !dataStart) return null

  const frames = Math.floor(dataLength / (2 * channels))
  if (!frames) return null

  const step = Math.max(1, Math.floor(frames / buckets))
  const peaks = new Float32Array(buckets)

  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const from = bucket * step
    const to = Math.min(frames, from + step)
    let peak = 0
    for (let frame = from; frame < to; frame += 1) {
      for (let channel = 0; channel < channels; channel += 1) {
        const value = Math.abs(view.getInt16(dataStart + (frame * channels + channel) * 2, true))
        if (value > peak) peak = value
      }
    }
    peaks[bucket] = peak / 32768
  }
  return peaks
}

function measure(): void {
  const root = tracksEl.value
  const wave = root?.querySelector('.wave')
  if (!root || !wave) return
  const box = wave.getBoundingClientRect()
  const base = root.getBoundingClientRect()
  headOffset.value = box.left - base.left
  waveWidth.value = box.width
}

async function drawWaveforms(): Promise<void> {
  const buckets = Math.max(80, Math.floor((waveWidth.value || 480) / 3))
  await Promise.all(tracks.value.map(async (track) => {
    try {
      const response = await fetch(track.url)
      track.peaks = wavPeaks(await response.arrayBuffer(), buckets)
    } catch {
      track.peaks = null
    }
    track.ready = true
    repaint(track.name)
  }))
}

/* ── audio ────────────────────────────────────────────────── */

function audible(track: TrackView): boolean {
  const anySolo = tracks.value.some((item) => item.solo)
  return !track.muted && (!anySolo || track.solo)
}

function applyMix(): void {
  for (const track of tracks.value) {
    const gain = gains.get(track.name)
    if (gain) gain.gain.value = audible(track) ? track.volume : 0
  }
}

async function load(): Promise<void> {
  const names = STEM_ORDER.filter((name) => props.job.stems[name])
  tracks.value = names.map((name) => ({
    name,
    url: props.job.stems[name],
    muted: false,
    solo: false,
    volume: 1,
    peaks: null,
    ready: false,
  }))

  await nextTick()
  measure()

  const AudioCtor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  ctx = new AudioCtor()

  for (const track of tracks.value) {
    const audio = new Audio()
    audio.preload = 'auto'
    audio.src = track.url
    const source = ctx.createMediaElementSource(audio)
    const gain = ctx.createGain()
    source.connect(gain).connect(ctx.destination)
    audios.set(track.name, audio)
    gains.set(track.name, gain)
  }

  await Promise.all(tracks.value.map((track) => new Promise<void>((resolve) => {
    const audio = audios.get(track.name)
    if (!audio) return resolve()
    if (audio.readyState >= 1) return resolve()
    audio.addEventListener('loadedmetadata', () => resolve(), { once: true })
    audio.addEventListener('error', () => resolve(), { once: true })
    setTimeout(resolve, 4000)
  })))

  duration.value = Math.max(
    ...tracks.value.map((track) => audios.get(track.name)?.duration || 0),
    props.job.duration_sec || 0,
  )

  applyMix()
  await drawWaveforms()
}

function master(): HTMLAudioElement | undefined {
  return audios.get(tracks.value[0]?.name ?? '')
}

async function playAll(): Promise<void> {
  await ctx?.resume()
  const at = master()?.currentTime ?? 0
  for (const track of tracks.value) {
    const audio = audios.get(track.name)
    if (audio && Math.abs(audio.currentTime - at) > 0.05) audio.currentTime = at
  }
  await Promise.all(tracks.value.map((track) => audios.get(track.name)?.play().catch(() => undefined)))
  playing.value = true
  startClock()
}

function pauseAll(): void {
  for (const track of tracks.value) audios.get(track.name)?.pause()
  playing.value = false
}

function togglePlay(): void {
  if (!tracks.value.length) return
  if (playing.value) pauseAll()
  else void playAll()
}

function seekTo(seconds: number): void {
  const target = Math.min(Math.max(0, seconds), duration.value || 0)
  for (const track of tracks.value) {
    const audio = audios.get(track.name)
    if (audio) audio.currentTime = target
  }
  position.value = target
}

function startClock(): void {
  if (raf) cancelAnimationFrame(raf)
  const frame = () => {
    const lead = master()
    if (!lead) return
    const now = lead.currentTime
    for (const track of tracks.value.slice(1)) {
      const audio = audios.get(track.name)
      if (audio && Math.abs(audio.currentTime - now) > 0.08) audio.currentTime = now
    }
    position.value = now
    if (playing.value && loop.value && duration.value && now >= duration.value - 0.08) seekTo(0)
    raf = requestAnimationFrame(frame)
  }
  raf = requestAnimationFrame(frame)
}

function toggleMute(track: TrackView): void {
  track.muted = !track.muted
  applyMix()
}

function toggleSolo(track: TrackView): void {
  track.solo = !track.solo
  applyMix()
}

function onVolume(track: TrackView, event: Event): void {
  track.volume = Number((event.target as HTMLInputElement).value) / 100
  applyMix()
}

function seekFromPointer(event: PointerEvent): void {
  const element = seekEl.value
  if (!element) return
  const box = element.getBoundingClientRect()
  seekTo(((event.clientX - box.left) / box.width) * duration.value)
}

function onSeekDown(event: PointerEvent): void {
  dragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  seekFromPointer(event)
}

function onSeekMove(event: PointerEvent): void {
  if (dragging.value) seekFromPointer(event)
}

function onSeekUp(event: PointerEvent): void {
  dragging.value = false
  ;(event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId)
}

function onKey(event: KeyboardEvent): void {
  if (event.code !== 'Space') return
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'BUTTON'].includes(target.tagName)) return
  event.preventDefault()
  togglePlay()
}

function onResize(): void {
  measure()
  for (const track of tracks.value) repaint(track.name)
}

onMounted(async () => {
  await load()
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onResize)
  for (const audio of audios.values()) {
    try {
      audio.pause()
    } catch {
      /* element may already be detached */
    }
  }
  audios.clear()
  gains.clear()
  canvases.clear()
  ctx?.close().catch(() => undefined)
  ctx = null
})
</script>

<template>
  <div class="wb-head">
    <button class="btn btn-ghost" @click="$emit('back')">
      <svg class="i-back" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 6l-6 6 6 6" /></svg>
      任务列表
    </button>
    <div class="wb-title-group">
      <h2>{{ job.filename }}</h2>
      <p class="wb-sub mono">{{ job.tier }} · {{ fmtTime(job.duration_sec) }}</p>
    </div>
    <div class="wb-actions">
      <a class="btn btn-primary" :href="zipUrl">下载全部</a>
    </div>
  </div>

  <div ref="tracksEl" class="tracks">
    <article
      v-for="track in tracks"
      :key="track.name"
      class="track"
      :class="{ 'is-pending': !track.ready }"
      :data-stem="track.name"
      :data-muted="track.muted"
      :data-dimmed="!audible(track)"
    >
      <div class="track-head">
        <span class="track-name">{{ label(track.name) }}</span>
        <div class="track-ms">
          <button
            class="ms"
            :aria-pressed="track.muted"
            title="静音"
            @click="toggleMute(track)"
          >M</button>
          <button
            class="ms"
            :aria-pressed="track.solo"
            title="独奏"
            @click="toggleSolo(track)"
          >S</button>
        </div>
        <input
          class="track-vol"
          type="range"
          min="0"
          max="100"
          :value="Math.round(track.volume * 100)"
          :aria-label="`${label(track.name)} 音量`"
          @input="onVolume(track, $event)"
        >
        <a
          class="track-dl"
          :href="track.url"
          :download="downloadName(track.name)"
          :title="`下载 ${downloadName(track.name)}`"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14" />
          </svg>
        </a>
      </div>
      <div class="wave" :style="{ '--p': progressPct }">
        <canvas :ref="(el) => registerCanvas(track.name, 'base', el)" class="wave-base" />
        <canvas :ref="(el) => registerCanvas(track.name, 'lit', el)" class="wave-lit" />
      </div>
    </article>

    <div
      class="playhead"
      :class="{ 'is-visible': playheadVisible }"
      :style="{ transform: `translateX(${playheadX})` }"
      aria-hidden="true"
    ><span /></div>
  </div>

  <div class="transport">
    <button
      class="play-btn"
      :class="{ 'is-playing': playing }"
      :aria-label="playing ? '暂停' : '播放'"
      @click="togglePlay"
    >
      <svg class="i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" /></svg>
      <svg class="i-pause" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7.5 5h3.2v14H7.5zM13.3 5h3.2v14h-3.2z" />
      </svg>
    </button>
    <span class="mono time">{{ currentTime }}</span>
    <div
      ref="seekEl"
      class="seek"
      role="slider"
      tabindex="0"
      aria-label="播放进度"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="Math.round(ratio * 100)"
      @pointerdown="onSeekDown"
      @pointermove="onSeekMove"
      @pointerup="onSeekUp"
      @keydown.left.prevent="seekTo(position - 5)"
      @keydown.right.prevent="seekTo(position + 5)"
    >
      <div class="seek-fill" :style="{ width: progressPct }" />
      <div class="seek-knob" :style="{ left: progressPct }" />
    </div>
    <span class="mono time dim">{{ totalTime }}</span>
    <button
      class="icon-btn loop"
      :aria-pressed="loop"
      aria-label="循环播放"
      title="循环播放"
      @click="loop = !loop"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 9V7a3 3 0 0 1 3-3h10l-2.2-2.2M20 15v2a3 3 0 0 1-3 3H7l2.2 2.2" />
        <path d="M17 4l2.8 2.8M7 20l-2.8-2.8" />
      </svg>
    </button>
  </div>
</template>
