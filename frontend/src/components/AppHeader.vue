<script setup lang="ts">
import { computed } from 'vue'

import type { Health } from '@/api/types'

const props = defineProps<{
  health: Health | null
  weights: string
}>()

defineEmits<{ (event: 'health'): void }>()

const providerLabel = computed(() => {
  const active = props.health?.active_providers?.[0] ?? 'CPUExecutionProvider'
  const short = active.replace('ExecutionProvider', '')
  // "CUDA" when the probe succeeded, "CPU 兜底" when it fell back — the chip
  // is the only place a user learns the GPU was not used.
  if (props.health?.gpu_ready) return short
  return short === 'CPU' ? 'CPU 兜底' : short
})

const providerTitle = computed(() => {
  const reason = props.health?.gpu_reason
  if (props.health?.gpu_ready) return 'ONNX Runtime 正在使用 CUDA 执行器'
  return reason ? `ONNX Runtime 执行器\n${reason}` : 'ONNX Runtime 执行器'
})
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
        <g stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <path d="M6 19v-6" /><path d="M11 23V9" /><path d="M16 21V11" />
          <path d="M21 18v-4" /><path d="M26 16v-.5" />
        </g>
      </svg>
      <div class="brand-text">
        <span class="brand-name">PitchMorph</span>
        <span class="brand-sub">stem separator</span>
      </div>
    </div>
    <div class="topbar-right">
      <span
        class="chip"
        :class="{ 'chip-gpu': health?.gpu_ready }"
        :title="providerTitle"
      >{{ providerLabel }}</span>
      <span class="chip" title="权重缓存状态">{{ weights }}</span>
      <button class="icon-btn" aria-label="环境自检" title="环境自检" @click="$emit('health')">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 2.7 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 9.6 4.1a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 19.4 11a2 2 0 1 1 0 4Z" />
        </svg>
      </button>
    </div>
  </header>
</template>
