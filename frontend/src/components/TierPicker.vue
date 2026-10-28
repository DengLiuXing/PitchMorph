<script setup lang="ts">
import type { ModelTier } from '@/api/types'

defineProps<{
  tiers: ModelTier[]
  tier: string | null
}>()

defineEmits<{ (event: 'select-tier', key: string): void }>()
</script>

<template>
  <div class="compose-bar">
    <div class="segmented" role="radiogroup" aria-label="分离档位">
      <button
        v-for="item in tiers"
        :key="item.key"
        type="button"
        class="seg"
        role="radio"
        :aria-checked="item.key === tier"
        :data-cached="item.cached ? 'true' : undefined"
        :title="item.detail"
        @click="$emit('select-tier', item.key)"
      >
        <span class="seg-label">{{ item.label }}</span>
        <span class="seg-meta">{{ item.download_mb }} MB · {{ item.eta }}</span>
      </button>
    </div>
  </div>
</template>
