<script setup lang="ts">
import { ArrowLeft, Waves } from 'lucide-vue-next'

import type { Health } from '@/api/types'

defineProps<{
  health: Health | null
  view: 'home' | 'workbench'
}>()

defineEmits<{ (event: 'back'): void }>()
</script>

<template>
  <header
    class="fixed inset-x-0 top-0 z-20 flex h-16 items-center gap-4 border-b border-white/[0.06] bg-ink-900/85 px-6 backdrop-blur-xl"
  >
    <div class="flex items-center gap-2.5">
      <span
        class="flex h-7 w-7 items-center justify-center border border-amber/40 bg-amber/10 text-amber-bright"
      >
        <Waves :size="15" />
      </span>
      <div class="flex flex-col leading-none">
        <span class="text-[15px] font-semibold tracking-tight text-chalk">PitchMorph</span>
        <span class="mt-1 text-[10px] uppercase tracking-[0.18em] text-chalk-mute">
          stems · pitch · midi
        </span>
      </div>
    </div>

    <button
      v-if="view === 'workbench'"
      type="button"
      class="btn btn-ghost ml-2 cursor-pointer"
      @click="$emit('back')"
    >
      <ArrowLeft :size="13" />
      返回队列
    </button>

    <div class="ml-auto flex items-center gap-4">
      <div class="flex items-center gap-2">
        <span class="relative flex h-1.5 w-1.5">
          <span
            class="absolute inset-0 rounded-full animate-pulseRing"
            :class="health ? 'bg-teal' : 'bg-red-400'"
          />
          <span
            class="relative h-1.5 w-1.5 rounded-full"
            :class="health ? 'bg-teal' : 'bg-red-400'"
          />
        </span>
        <span class="num text-[11px] text-chalk-dim">
          {{ health ? '引擎就绪' : '未连接' }}
        </span>
      </div>
      <span v-if="health" class="chip">
        {{ health.ffmpeg ? 'ffmpeg ✓' : 'ffmpeg ✗' }}
      </span>
      <span v-if="health" class="chip">
        上限 {{ health.max_upload_mb }} MB / {{ Math.round(health.max_duration_sec / 60) }} 分钟
      </span>
    </div>
  </header>
</template>
