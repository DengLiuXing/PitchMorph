<script setup lang="ts">
import { Download, Package, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import type { Job, StemName } from '@/api/types'
import { STEMS, STEM_LABELS } from '@/api/types'

const props = defineProps<{ job: Job }>()

const open = ref(false)

// 「人声」档只落一条轨，按服务端列表渲染
const stems = computed(() => (props.job.sources.length > 0 ? props.job.sources : [...STEMS]))

function labelOf(name: string): string {
  return STEM_LABELS[name as StemName] ?? name
}

const EXPORTS: { kind: string; label: string; hint: string }[] = [
  { kind: 'notes_midi', label: '音符 MIDI', hint: '离散音符，任何 DAW 都能读' },
  { kind: 'bend_midi', label: '弯音 MIDI', hint: '长音符 + 密集弯音，还原原始曲线' },
  { kind: 'ustx', label: 'OpenUtau 工程', hint: '导入后需在轨道里选音源才能出声' },
]

function stemHref(stem: string): string {
  return `/api/jobs/${props.job.id}/stems/${stem}`
}

// 中值滤波窗口（帧）。1 = 关，窗口越大曲线越平，但颤音也会被抹掉
const smooth = ref(3)
const SMOOTH_OPTIONS = [1, 3, 5, 9]

function exportHref(kind: string): string {
  return `/api/jobs/${props.job.id}/exports/${kind}?smooth=${smooth.value}`
}

/** 音轨与工程分开打包：用途不同，混在一个包里还得手动挑 */
function packageHref(kind: 'stems' | 'project'): string {
  const base = `/api/jobs/${props.job.id}/package/${kind}`
  // 音轨是成品 WAV，与平滑度无关；工程包要带参数，MIDI/USTX 是按它即时生成的
  return kind === 'stems' ? base : `${base}?smooth=${smooth.value}`
}
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="btn btn-primary cursor-pointer"
      :aria-expanded="open"
      @click="open = !open"
    >
      <Download :size="13" />
      导出
    </button>

    <template v-if="open">
      <div class="fixed inset-0 z-20" @click="open = false" />

      <div
        class="absolute right-0 top-full z-30 mt-2 w-[292px] border border-white/[0.08] bg-ink-800 p-3 shadow-2xl animate-rise"
      >
        <div class="mb-2 flex items-center justify-between">
          <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">分轨 WAV</span>
          <button
            type="button"
            class="cursor-pointer text-chalk-mute hover:text-chalk"
            aria-label="关闭"
            @click="open = false"
          >
            <X :size="13" />
          </button>
        </div>

        <div class="mb-3 grid grid-cols-2 gap-1.5">
          <a
            v-for="stem in stems"
            :key="stem"
            :href="stemHref(stem)"
            class="btn cursor-pointer !justify-start"
          >
            <Download :size="11" />
            {{ labelOf(stem) }}
          </a>
        </div>

        <a :href="packageHref('stems')" class="btn mb-4 w-full !justify-start">
          <Package :size="12" />
          打包下载音轨（zip）
        </a>

        <div class="mb-2 flex items-center gap-2">
          <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">音高导出</span>
          <span
            v-if="!job.has_exports"
            class="text-[10.5px] text-chalk-mute"
          >音高完成后可用</span>
        </div>

        <div class="mb-2 flex items-center gap-1.5">
          <span class="text-[10.5px] text-chalk-mute">平滑</span>
          <button
            v-for="option in SMOOTH_OPTIONS"
            :key="option"
            type="button"
            class="cursor-pointer rounded px-1.5 py-0.5 text-[10.5px] transition-colors duration-150"
            :class="
              smooth === option
                ? 'bg-amber/20 text-amber-bright'
                : 'text-chalk-mute hover:text-chalk'
            "
            :title="option === 1 ? '不平滑，保留原始曲线' : `中值滤波 ${option} 帧`"
            @click="smooth = option"
          >
            {{ option === 1 ? '关' : `${option} 帧` }}
          </button>
        </div>

        <ul class="flex flex-col gap-1.5">
          <li v-for="item in EXPORTS" :key="item.kind">
            <a
              :href="job.has_exports ? exportHref(item.kind) : undefined"
              class="flex flex-col gap-0.5 border border-white/[0.08] px-2.5 py-2 transition-colors duration-150"
              :class="
                job.has_exports
                  ? 'cursor-pointer hover:border-amber/40 hover:bg-amber/[0.06]'
                  : 'pointer-events-none opacity-40'
              "
            >
              <span class="flex items-center gap-1.5 text-[12px] text-chalk">
                <Download :size="11" />
                {{ item.label }}
              </span>
              <span class="pl-[18px] text-[10.5px] leading-relaxed text-chalk-mute">
                {{ item.hint }}
              </span>
            </a>
          </li>
        </ul>

        <a
          v-if="job.has_exports"
          :href="packageHref('project')"
          class="btn mt-2 w-full !justify-start"
        >
          <Package :size="12" />
          打包下载工程（zip）
        </a>
      </div>
    </template>
  </div>
</template>
