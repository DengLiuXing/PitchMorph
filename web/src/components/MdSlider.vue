<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ modelValue: number; label: string; disabled?: boolean }>()
const emit = defineEmits<{ (event: 'update:modelValue', value: number): void }>()

/** MD3 滑块的已填充段靠渐变实现，需要把当前值换算成百分比 */
const trackStyle = computed(() => ({ '--v': `${Math.min(100, Math.max(0, props.modelValue))}%` }))
</script>

<template>
  <input
    class="md-slider"
    type="range"
    min="0"
    max="100"
    :value="modelValue"
    :style="trackStyle"
    :aria-label="label"
    :disabled="disabled"
    @input="emit('update:modelValue', Number(($event.target as HTMLInputElement).value))"
  />
</template>
