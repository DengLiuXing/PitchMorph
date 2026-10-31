<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: number; indeterminate?: boolean }>()

const percent = computed(() => Math.round(Math.min(1, Math.max(0, props.value)) * 100))
</script>

<template>
  <div
    class="md-progress"
    :class="{ 'md-progress--indeterminate': indeterminate }"
    role="progressbar"
    :aria-valuemin="0"
    :aria-valuemax="100"
    :aria-valuenow="indeterminate ? undefined : percent"
  >
    <span :style="indeterminate ? undefined : { width: `${percent}%` }" />
  </div>
</template>
