<script setup lang="ts">
import { AlertTriangle, ChevronDown, Download, Loader2, Maximize2, Minus, Plus } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { JobMeta, Peaks, PitchCurve } from '@/api/types'
import { STEM_LABELS, isActive, stemRank } from '@/api/types'
import MdIconButton from '@/components/MdIconButton.vue'
import MdSlider from '@/components/MdSlider.vue'
import PitchMonitor from '@/components/PitchMonitor.vue'
import RulerLane from '@/components/RulerLane.vue'
import TrackMenu from '@/components/TrackMenu.vue'
import Transport from '@/components/Transport.vue'
import WaveLane from '@/components/WaveLane.vue'
import { usePlayer } from '@/composables/usePlayer'
import { tickStep } from '@/lib/canvas'
import { formatTime } from '@/lib/format'
import { stemColor } from '@/lib/md3'

const props = defineProps<{ job: JobMeta }>()
const emit = defineEmits<{ (event: 'refresh'): void }>()

/** 轨头列宽：canvas 宽度计算要用，CSS 里的 --head-w 与它保持一致 */
const HEAD_W = 208
const MIN_PPS = 0.4
const MAX_PPS = 800

const player = usePlayer()
const {
  playing,
  position,
  volumes,
  muted,
  solo,
  attach,
  setDuration,
  pause,
  toggle,
  seek,
  nudge,
  toggleMute,
  toggleSolo,
  setVolume,
  isSilent,
} = player

/** 返回队列时调用：视图只是切换显示，组件没卸载，不主动暂停就会继续出声 */
defineExpose({ stop: pause })

const scroller = ref<HTMLDivElement | null>(null)
const viewW = ref(1)
const scrollX = ref(0)
const pps = ref(1)
const userZoom = ref(false)
const scrub = ref<number | null>(null)
const notice = ref('')

const peaks = ref<Record<string, Peaks>>({})
const curves = ref<Record<string, PitchCurve>>({})
const curveLoading = ref<string[]>([])

/** 音高监视器只有一个：显示谁的曲线由这里决定，切轨就整体替换 */
const monitor = ref<string | null>(null)

/** 轨头操作菜单（fixed 浮层，不撑开轨头） */
const menu = ref<{ name: string; x: number; y: number } | null>(null)

const tracks = computed(() =>
  // 按后端返回的 stems 渲染：「仅人声」档只有一条，写死四条会渲染出 404 空轨
  [...props.job.stems]
    .sort((left, right) => stemRank(left.name) - stemRank(right.name))
    .map((stem) => ({
      name: stem.name,
      label: STEM_LABELS[stem.name] ?? stem.name,
      color: stemColor(stem.name),
      url: api.stemUrl(props.job.id, stem.name),
      downloadName: api.downloadName(props.job.filename, stem.name),
    })),
)

const hasStems = computed(() => props.job.stems.length > 0)
const running = computed(() => isActive(props.job.state))
const totalSeconds = computed(() => props.job.duration ?? props.job.stems[0]?.duration ?? 0)
const timelineW = computed(() => Math.max(viewW.value, totalSeconds.value * pps.value))
const displayTime = computed(() => scrub.value ?? position.value)
const percent = computed(() => Math.round((props.job.fraction ?? 0) * 100))
const fitPps = computed(() =>
  totalSeconds.value > 0 ? clamp(viewW.value / totalSeconds.value) : 1,
)
const gridStep = computed(() => Math.max(8, tickStep(pps.value) * pps.value))
const hint = computed(() => {
  if (solo.value.length > 0) return `${solo.value.map((name) => STEM_LABELS[name] ?? name).join(' + ')} solo`
  if (muted.value.length > 0) return `静音 ${muted.value.length} 轨`
  return `${tracks.value.length} 轨混音`
})
const monitorTrack = computed(() => tracks.value.find((track) => track.name === monitor.value) ?? null)
const monitorPitch = computed(() => (monitor.value ? props.job.pitch?.[monitor.value] : undefined))

function clamp(value: number): number {
  return Math.min(MAX_PPS, Math.max(MIN_PPS, value))
}

// ---- 缩放与滚动 ----

watch([viewW, totalSeconds], () => {
  if (!userZoom.value) pps.value = fitPps.value
})

function measure(): void {
  const element = scroller.value
  if (!element) return
  viewW.value = Math.max(1, element.clientWidth - HEAD_W)
  scrollX.value = element.scrollLeft
}

let frame = 0
let scrollQueued = false
function onScroll(): void {
  if (scrollQueued) return // 只合并不取消：否则连续滚动时回调会被反复重排
  scrollQueued = true
  frame = requestAnimationFrame(() => {
    scrollQueued = false
    if (scroller.value) scrollX.value = scroller.value.scrollLeft
  })
}

/** factor > 1 放大。给了 clientX 就以指针位置为锚，否则以视口中心为锚 */
function zoom(factor: number, clientX?: number): void {
  const element = scroller.value
  const next = clamp(pps.value * factor)
  if (next === pps.value) return
  const focusX =
    clientX !== undefined && element ? clientX - element.getBoundingClientRect().left : viewW.value / 2
  const anchorTime = (scrollX.value + focusX) / pps.value
  userZoom.value = true
  pps.value = next
  void nextTick(() => {
    if (!element) return
    element.scrollLeft = Math.max(0, anchorTime * next - focusX)
    scrollX.value = element.scrollLeft
  })
}

function fit(): void {
  userZoom.value = false
  pps.value = fitPps.value
  void nextTick(() => {
    if (scroller.value) scroller.value.scrollLeft = 0
    scrollX.value = 0
  })
}

function onWheel(event: WheelEvent): void {
  if (!event.ctrlKey && !event.metaKey) return // 普通滚轮交给原生横/纵向滚动
  event.preventDefault()
  zoom(Math.exp(-event.deltaY * 0.0015), event.clientX)
}

// ---- 定位：波形与标尺共用 ----

let dragging = false

function secondsAt(event: PointerEvent): number {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width === 0) return 0
  // rect 已经包含横向滚动，所以不用再加 scrollX
  return Math.min(totalSeconds.value, Math.max(0, (event.clientX - rect.left) / pps.value))
}

function onDown(event: PointerEvent): void {
  const element = event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  dragging = true
  scrub.value = secondsAt(event)
}

function onMove(event: PointerEvent): void {
  if (dragging) scrub.value = secondsAt(event)
}

function onUp(event: PointerEvent): void {
  if (!dragging) return
  dragging = false
  const element = event.currentTarget as HTMLElement
  if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId)
  seek(secondsAt(event))
  scrub.value = null
}

// ---- 音频、波形与音高 ----

function register(name: string, element: Element | null): void {
  if (!(element instanceof HTMLAudioElement)) return
  attach(name, element)
}

function loadPeaks(): void {
  if (totalSeconds.value > 0) setDuration(totalSeconds.value)
  for (const stem of props.job.stems) {
    if (peaks.value[stem.name]) continue
    api
      .peaks(props.job.id, stem.name)
      .then((data) => (peaks.value = { ...peaks.value, [stem.name]: data }))
      .catch(() => undefined) // 波形还没写完就先空着，不打断页面
  }
}

watch([() => props.job.stems, totalSeconds], loadPeaks, { immediate: true })

/** 播放时把播放头保持在视口内，超出就往回追 20% */
watch(position, (value) => {
  const element = scroller.value
  if (!playing.value || !element) return
  const x = value * pps.value
  if (x < element.scrollLeft + 24 || x > element.scrollLeft + viewW.value - 24) {
    element.scrollLeft = Math.max(0, x - viewW.value * 0.2)
  }
})

function loadCurve(name: string): void {
  if (curves.value[name] || curveLoading.value.includes(name)) return
  curveLoading.value = [...curveLoading.value, name]
  api
    .pitch(props.job.id, name)
    .then((data) => {
      if (Array.isArray(data.times) && data.times.length > 0) {
        curves.value = { ...curves.value, [name]: data as PitchCurve }
      }
    })
    .catch((issue: Error) => (notice.value = issue.message))
    .finally(() => (curveLoading.value = curveLoading.value.filter((item) => item !== name)))
}

/** 打开/切换监视器：同一轨再点一次就关掉，换轨则是整体替换内容 */
async function toggleMonitor(name: string): Promise<void> {
  menu.value = null
  if (monitor.value === name) {
    monitor.value = null
    return
  }
  monitor.value = name

  const state = props.job.pitch?.[name]?.state
  if (state === 'ready') {
    loadCurve(name)
    return
  }
  if (state === 'queued' || state === 'running') return // 轮询回来后自动取曲线
  try {
    await api.startPitch(props.job.id, name)
    emit('refresh')
  } catch (issue) {
    notice.value = (issue as Error).message
  }
}

function openMenu(name: string, event: MouseEvent): void {
  if (menu.value?.name === name) {
    menu.value = null
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  menu.value = { name, x: Math.min(rect.left, window.innerWidth - 212), y: rect.bottom + 4 }
}

// 后端在分轨后会为每条轨自动算音高，算完这里把曲线取回来
watch(
  () => props.job.pitch,
  (value) => {
    if (monitor.value && value?.[monitor.value]?.state === 'ready') loadCurve(monitor.value)
  },
  { deep: true, immediate: true },
)

// ---- 快捷键 ----

function onKeydown(event: KeyboardEvent): void {
  const tag = (event.target as HTMLElement | null)?.tagName ?? ''
  if (tag === 'INPUT' || tag === 'TEXTAREA') return

  if (event.code === 'Space') {
    event.preventDefault() // 否则焦点在按钮上会再触发一次 click
    toggle()
  } else if (event.code === 'ArrowLeft') {
    event.preventDefault()
    nudge(-5)
  } else if (event.code === 'ArrowRight') {
    event.preventDefault()
    nudge(5)
  } else if (event.code === 'Equal') {
    zoom(1.5)
  } else if (event.code === 'Minus') {
    zoom(1 / 1.5)
  }
}

let observer: ResizeObserver | null = null

onMounted(() => {
  observer = new ResizeObserver(measure)
  if (scroller.value) observer.observe(scroller.value)
  measure()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', measure)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', measure)
  cancelAnimationFrame(frame)
})
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 进度只在顶栏下沿长一条细线，不占布局 -->
    <div v-if="running" class="h-1 shrink-0 bg-surface-container-high" aria-hidden="true">
      <span
        class="block h-full bg-primary transition-[width] duration-500 ease-md-standard"
        :style="{ width: `${percent}%` }"
      />
    </div>

    <div
      v-if="notice"
      class="shrink-0 bg-error-container px-4 py-1.5 md-label-medium text-on-error-container"
    >
      {{ notice }}
    </div>

    <!-- 还没出音轨：一行提示就够，进度看顶栏，别用整块卡片吃掉页面 -->
    <div v-if="!hasStems" class="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <Loader2 v-if="running" :size="26" class="animate-spin text-primary" />
      <AlertTriangle v-else :size="26" class="text-error" />
      <p class="md-body-large text-on-surface">
        {{ running ? '分轨进行中，完成后轨道会出现在这里' : job.state === 'cancelled' ? '任务已取消' : '任务失败' }}
      </p>
      <p class="md-body-medium max-w-[460px] text-on-surface-variant">
        {{ job.failure || job.message || '排队中' }}
      </p>
    </div>

    <template v-else>
      <!-- 单滚动容器：标尺 sticky top、轨头 sticky left，天然对齐 -->
      <div ref="scroller" class="timeline" @scroll.passive="onScroll" @wheel="onWheel">
        <div class="timeline-inner" :style="{ width: `${HEAD_W + timelineW}px` }">
          <div class="ruler-row">
            <div class="ruler-gutter">
              <span class="md-label-medium text-on-surface-variant">时间轴</span>
              <span class="flex shrink-0 items-center gap-0.5">
                <MdIconButton small label="缩小（-）" @click="zoom(1 / 1.5)">
                  <Minus :size="16" />
                </MdIconButton>
                <MdIconButton small label="放大（+）" @click="zoom(1.5)">
                  <Plus :size="16" />
                </MdIconButton>
                <MdIconButton small label="适配整首" @click="fit()">
                  <Maximize2 :size="16" />
                </MdIconButton>
              </span>
            </div>
            <div
              class="ruler-track"
              @pointerdown="onDown"
              @pointermove="onMove"
              @pointerup="onUp"
              @pointercancel="onUp"
            >
              <RulerLane :pps="pps" :scroll-x="scrollX" :view-width="viewW" />
            </div>
          </div>

          <div v-for="track in tracks" :key="track.name" class="lane-row">
            <div class="lane-head" :data-dimmed="isSilent(track.name) ? 'true' : 'false'">
              <div class="flex items-center gap-1">
                <span class="lane-name" :title="track.label">
                  <i class="lane-swatch" :style="{ background: track.color }" />
                  <span class="truncate">{{ track.label }}</span>
                  <span
                    v-if="monitor === track.name"
                    class="md-chip md-chip--primary md-label-small !h-[18px] !px-1.5"
                  >
                    音高
                  </span>
                </span>

                <MdIconButton small label="轨道操作" @click="openMenu(track.name, $event)">
                  <ChevronDown :size="16" />
                </MdIconButton>

                <a
                  class="md-icon-btn md-icon-btn--sm"
                  :href="track.url"
                  :download="track.downloadName"
                  :title="`下载 ${track.downloadName}`"
                >
                  <Download :size="16" />
                </a>
              </div>

              <div class="flex items-center gap-1.5">
                <div class="lane-ms">
                  <MdIconButton
                    small
                    act="mute"
                    label="静音"
                    :pressed="muted.includes(track.name)"
                    @click="toggleMute(track.name)"
                  >
                    <span class="md-label-small">M</span>
                  </MdIconButton>
                  <MdIconButton
                    small
                    act="solo"
                    label="独奏"
                    :pressed="solo.includes(track.name)"
                    @click="toggleSolo(track.name)"
                  >
                    <span class="md-label-small">S</span>
                  </MdIconButton>
                </div>

                <MdSlider
                  class="lane-vol"
                  :model-value="Math.round((volumes[track.name] ?? 0.9) * 100)"
                  :label="`${track.label} 音量`"
                  @update:model-value="setVolume(track.name, $event / 100)"
                />
              </div>
            </div>

            <div
              class="lane-track"
              :data-dimmed="isSilent(track.name) ? 'true' : 'false'"
              @pointerdown="onDown"
              @pointermove="onMove"
              @pointerup="onUp"
              @pointercancel="onUp"
            >
              <WaveLane
                :peaks="peaks[track.name] ?? null"
                :duration="totalSeconds"
                :pps="pps"
                :scroll-x="scrollX"
                :view-width="viewW"
                :color="track.color"
              />
            </div>
          </div>

          <div class="lane-filler" :style="{ backgroundSize: `${gridStep}px 100%` }" aria-hidden="true" />

          <div
            class="playhead"
            :style="{ left: `${HEAD_W + displayTime * pps}px` }"
            aria-hidden="true"
          />
        </div>
      </div>

      <!-- 全页唯一的音高监视器：换轨就整体替换，不叠加 -->
      <div v-if="monitorTrack" class="shrink-0 px-2 pt-2">
        <PitchMonitor
          :label="monitorTrack.label"
          :color="monitorTrack.color"
          :curve="curves[monitorTrack.name] ?? null"
          :duration="totalSeconds"
          :position="position"
          :dimmed="isSilent(monitorTrack.name)"
          :pending="['queued', 'running'].includes(monitorPitch?.state ?? '')"
          :percent="Math.round((monitorPitch?.fraction ?? 0) * 100)"
          :failure="monitorPitch?.failure ?? null"
          @close="monitor = null"
        />
      </div>

      <Transport
        :playing="playing"
        :position="position"
        :duration="totalSeconds"
        :hint="hint"
        @toggle="toggle"
        @seek="seek"
      />

      <div class="hidden">
        <audio
          v-for="track in tracks"
          :key="track.url"
          :src="track.url"
          preload="auto"
          crossorigin="anonymous"
          :ref="(element) => register(track.name, element as Element | null)"
        />
      </div>
    </template>

    <TrackMenu
      v-if="menu"
      :x="menu.x"
      :y="menu.y"
      :pitch-on="monitor === menu.name"
      @pitch="toggleMonitor(menu.name)"
      @close="menu = null"
    />
  </div>
</template>
