<script setup lang="ts">
withDefaults(defineProps<{ variant?: 'default' | 'primary' | 'tertiary' | 'flat' }>(), {
  variant: 'default',
})
</script>

<template>
  <span class="md-chip" :class="{ 'md-chip--primary': variant === 'primary', 'md-chip--tertiary': variant === 'tertiary', 'md-chip--flat': variant === 'flat' }">
    <slot name="icon" />
    <slot />
  </span>
</template>
