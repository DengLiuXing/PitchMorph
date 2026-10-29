<script setup lang="ts">
import { Pause, Play, X } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { Job } from '@/api/types'
import { STEM_LABELS } from '@/api/types'
import ExportPanel from '@/components/ExportPanel.vue'
import PitchView from '@/components/PitchView.vue'
import TrackRow from '@/components/TrackRow.vue'
import { usePlayer } from '@/composables/usePlayer'
import { formatTime } from '@/lib/format'

const props = defineProps<{ job: Job }>()

const TIER_TEXT: Record<string, string> = {
  vocals: '人声',
  fast: '四轨 · 快',
  quality: '四轨 · 质量',
}

const {
  playing,
  position,
  duration,
  volumes,
  muted,
  solo,
  attach,
  pause,
  toggle,
  seek,
  nudge,
  toggleMute,
  toggleSolo,
  setVolume,
  reset,
} = usePlayer()

/** 返回队列时调用：视图只是 display:none，组件没卸载，不主动暂停就会继续出声 */
defineExpose({ stop: pause })

const listRef = ref<HTMLDivElement | null>(null)
const seekRef = ref<HTMLDivElement | null>(null)
const headOffset = ref(0)
const waveWidth = ref(0)
const showPitch = ref(false)

let scrubbing = false

const tracks = computed(() =>
  // 按服务端返回的真实列表渲染：人声档只有一条，跳过分轨的也只有一条原始音频。
  // 这里绝不能回退成四条 —— 那会渲染出三个 404 的空轨（改错过一次）。
  props.job.sources.map((name) => ({
    name,
    label: STEM_LABELS[name] ?? name,
    url: api.stemUrl(props.job.id, name),
    downloadUrl: api.downloadUrl(props.job.id, name),
    downloadName: api.downloadName(props.job.filename, name),
  })),
)

const ratio = computed(() => (duration.value > 0 ? Math.min(1, position.value / duration.value) : 0))

// 拖动时只改预览位置，松手才真正 seek：每帧改 currentTime 会让每个 <audio>
// 重新发 Range 请求，旧的被浏览器 abort，服务端就刷出一堆 ConnectionResetError
const scrubRatio = ref<number | null>(null)
const displayRatio = computed(() => scrubRatio.value ?? ratio.value)
const playheadX = computed(() => headOffset.value + displayRatio.value * waveWidth.value)

function isSilent(name: string): boolean {
  return solo.value.length > 0 ? !solo.value.includes(name) : muted.value.includes(name)
}

function onScrub(value: number): void {
  scrubRatio.value = value
}

function onCommit(): void {
  if (scrubRatio.value !== null) seek(scrubRatio.value * duration.value)
  scrubRatio.value = null
}

function measure(): void {
  const list = listRef.value
  const canvas = list?.querySelector('canvas')
  if (!list || !canvas) return
  const listBox = list.getBoundingClientRect()
  const box = canvas.getBoundingClientRect()
  headOffset.value = box.left - listBox.left
  waveWidth.value = box.width
}

function onReady(payload: { name: string; element: HTMLAudioElement }): void {
  attach(payload.name, payload.element)
}

function onVolume(payload: { name: string; value: number }): void {
  setVolume(payload.name, payload.value)
}

// ---- seek 条：pointer 拖拽 + 键盘 ----

function seekRatio(event: PointerEvent): number {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width === 0) return 0
  return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
}

function onSeekDown(event: PointerEvent): void {
  const element = event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  scrubbing = true
  onScrub(seekRatio(event))
}

function onSeekMove(event: PointerEvent): void {
  if (!scrubbing) return
  onScrub(seekRatio(event))
}

function onSeekUp(event: PointerEvent): void {
  if (!scrubbing) return
  scrubbing = false
  onScrub(seekRatio(event))
  onCommit()
  const element = event.currentTarget as HTMLElement
  if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId)
}

// ---- 全局快捷键 ----

function onKeydown(event: KeyboardEvent): void {
  const tag = (event.target as HTMLElement | null)?.tagName ?? ''
  if (tag === 'INPUT' || tag === 'TEXTAREA') return

  if (event.code === 'Space') {
    event.preventDefault() // 否则聚焦在按钮上会二次触发
    toggle()
  } else if (event.code === 'ArrowLeft') {
    event.preventDefault()
    nudge(-5)
  } else if (event.code === 'ArrowRight') {
    event.preventDefault()
    nudge(5)
  }
}

watch(
  () => props.job.id,
  () => {
    reset()
    showPitch.value = false
    void nextTick(measure)
  },
)

onMounted(() => {
  void nextTick(measure)
  window.addEventListener('resize', measure)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', measure)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="flex h-[calc(100vh-4rem)] flex-col">
    <div class="flex flex-wrap items-center gap-3 border-b border-white/[0.06] px-6 py-3">
      <div class="flex min-w-0 flex-col">
        <h2 class="truncate text-[15px] font-medium leading-tight text-chalk" :title="job.filename">
          {{ job.filename }}
        </h2>
        <p class="num mt-0.5 text-[11px] text-chalk-mute">
          {{ TIER_TEXT[job.tier] ?? job.tier }} · {{ formatTime(duration) }} ·
          {{ tracks.length }} 轨
        </p>
      </div>

      <div class="ml-auto flex items-center gap-1.5">
        <button
          v-if="job.has_pitch"
          type="button"
          class="btn cursor-pointer"
          :class="showPitch ? 'btn-primary' : ''"
          :aria-pressed="showPitch"
          @click="showPitch = !showPitch"
        >
          音高线
        </button>
        <ExportPanel :job="job" />
      </div>
    </div>

    <div class="relative min-h-0 flex-1">
      <div
        ref="listRef"
        class="relative flex h-full flex-col gap-1.5 overflow-y-auto px-6 py-2"
      >
      <TrackRow
        v-for="track in tracks"
        :key="`${track.name}-${track.url}`"
        :name="track.name"
        :label="track.label"
        :url="track.url"
        :download-url="track.downloadUrl"
        :download-name="track.downloadName"
        :progress="displayRatio"
        :volume="volumes[track.name] ?? 0.9"
        :muted="muted.includes(track.name)"
        :soloed="solo.includes(track.name)"
        :dimmed="isSilent(track.name)"
        @ready="onReady"
        @mute="toggleMute"
        @solo="toggleSolo"
        @scrub="onScrub"
        @commit="onCommit"
        @volume="onVolume"
      />

        <div
          class="playhead"
          :class="{ 'is-visible': playing || position > 0 }"
          :style="{ transform: `translateX(${playheadX}px)` }"
          aria-hidden="true"
        >
          <span />
        </div>
      </div>

      <!-- 音高是浮层，不挤压轨道：分辨率不够时轨道被压扁不符合直觉 -->
      <section
        v-if="showPitch && job.has_pitch"
        class="absolute inset-x-4 bottom-3 z-10 h-[252px] overflow-hidden rounded-xl border border-white/[0.12] bg-ink-800/95 shadow-[0_28px_70px_-28px_rgba(0,0,0,0.95)] backdrop-blur-md animate-rise"
      >
        <button
          type="button"
          class="absolute right-2.5 top-2.5 z-20 cursor-pointer rounded p-1 text-chalk-mute transition-colors hover:bg-white/[0.06] hover:text-chalk"
          aria-label="关闭音高线"
          @click="showPitch = false"
        >
          <X :size="14" />
        </button>
        <PitchView :job-id="job.id" :position="position" />
      </section>
    </div>

    <div class="border-t border-white/[0.06] px-6 py-3">
      <div class="transport">
        <button
          type="button"
          class="play-btn"
          :aria-label="playing ? '暂停（空格）' : '播放（空格）'"
          :title="playing ? '暂停（空格）' : '播放（空格）'"
          @click="toggle"
        >
          <Pause v-if="playing" :size="14" />
          <Play v-else :size="14" style="margin-left: 2px" />
        </button>

        <span class="num w-12 text-[12px] text-chalk">{{ formatTime(position) }}</span>

        <div
          ref="seekRef"
          class="seek"
          role="slider"
          tabindex="0"
          aria-label="播放进度"
          :aria-valuemin="0"
          :aria-valuemax="100"
          :aria-valuenow="Math.round(ratio * 100)"
          @pointerdown="onSeekDown"
          @pointermove="onSeekMove"
          @pointerup="onSeekUp"
          @pointercancel="onSeekUp"
          @keydown.left.prevent="nudge(-5)"
          @keydown.right.prevent="nudge(5)"
        >
          <div class="seek-fill" :style="{ width: `${displayRatio * 100}%` }" />
          <div class="seek-knob" :style="{ left: `${displayRatio * 100}%` }" />
        </div>

        <span class="num w-12 text-right text-[11px] text-chalk-mute">
          {{ formatTime(duration) }}
        </span>
      </div>
    </div>
  </div>
</template>
