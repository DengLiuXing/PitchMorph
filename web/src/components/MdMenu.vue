<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{ x: number; y: number }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const panel = ref<HTMLElement | null>(null)

// 菜单挂在 fixed 层：点面板外任意地方或按 Esc 都关掉
function onPointerDown(event: PointerEvent): void {
  if (panel.value && !panel.value.contains(event.target as Node)) emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('pointerdown', onPointerDown, true)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', onPointerDown, true)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    ref="panel"
    class="md-menu animate-menu-in origin-top-left"
    :style="{ left: `${x}px`, top: `${y}px` }"
    role="menu"
  >
    <slot />
  </div>
</template>
