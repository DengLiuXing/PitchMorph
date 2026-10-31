<script setup lang="ts">
defineProps<{ title: string; subtitle?: string }>()
</script>

<template>
  <header class="md-appbar shrink-0">
    <slot name="leading" />

    <div class="min-w-0 flex-1 pl-2">
      <h1 class="md-title-medium truncate text-on-surface" :title="title">{{ title }}</h1>
      <p v-if="subtitle" class="md-label-small truncate text-on-surface-variant">{{ subtitle }}</p>
    </div>

    <div class="flex shrink-0 items-center gap-1">
      <slot name="actions" />
    </div>
  </header>
</template>
