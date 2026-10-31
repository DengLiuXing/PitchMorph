<script setup lang="ts">
import { Check } from 'lucide-vue-next'

interface Option {
  value: string
  label: string
}

defineProps<{ modelValue: string; options: Option[]; label: string }>()
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()
</script>

<template>
  <div class="md-segmented" role="group" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="md-segment"
      :aria-pressed="modelValue === option.value"
      @click="emit('update:modelValue', option.value)"
    >
      <Check v-if="modelValue === option.value" :size="16" :stroke-width="2.4" />
      {{ option.label }}
    </button>
  </div>
</template>
