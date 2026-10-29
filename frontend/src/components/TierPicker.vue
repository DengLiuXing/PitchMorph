<script setup lang="ts">
import type { ModelList, Tier } from '@/api/types'

defineProps<{
  models: ModelList | null
  modelValue: string
}>()

defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const HINTS: Record<string, string> = {
  vocals: '只跑一个人声专家，最快；其余三轨不落盘，直接进音高分析',
  fast: '单模型一次出四轨，日常够用',
  quality: '四专家集成，慢约四倍',
}

function hint(tier: Tier): string {
  return HINTS[tier.id] ?? ''
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-3">
    <span class="text-[11px] uppercase tracking-[0.16em] text-chalk-mute">档位</span>

    <div class="flex overflow-hidden rounded-lg border border-white/[0.08] bg-ink-800/60">
      <button
        v-for="tier in models?.tiers ?? []"
        :key="tier.id"
        type="button"
        class="cursor-pointer px-3.5 py-1.5 text-[12px] transition-colors duration-150 focus-visible:outline-none"
        :class="
          modelValue === tier.id
            ? 'bg-amber/15 text-amber-bright'
            : 'text-chalk-dim hover:bg-white/[0.03] hover:text-chalk'
        "
        :title="hint(tier)"
        @click="$emit('update:modelValue', tier.id)"
      >
        {{ tier.label }}
      </button>
    </div>

    <span class="text-[11px] text-chalk-mute">
      {{ hint((models?.tiers ?? []).find((tier) => tier.id === modelValue) ?? { id: '', label: '' }) }}
    </span>
  </div>
</template>
