<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  formats: string
  limit: string
  busy: boolean
  error: string | null
}>()

const emit = defineEmits<{ (event: 'pick', files: File[]): void }>()

const dragging = ref(false)
const input = ref<HTMLInputElement | null>(null)

function onPick(event: Event): void {
  const element = event.target as HTMLInputElement
  const files = Array.from(element.files ?? [])
  element.value = '' // allow picking the same file twice
  if (files.length) emit('pick', files)
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length) emit('pick', files)
}
</script>

<template>
  <div class="compose">
    <div
      class="dropzone"
      :class="{ 'is-dragging': dragging, 'is-busy': busy }"
      tabindex="0"
      role="button"
      aria-label="选择或拖入音频文件"
      @click="input?.click()"
      @keydown.enter.prevent="input?.click()"
      @keydown.space.prevent="input?.click()"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input ref="input" type="file" hidden multiple @change="onPick">
      <div class="drop-inner">
        <svg class="drop-icon" viewBox="0 0 48 48" aria-hidden="true">
          <g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <path d="M6 30v-12M12 35V13M18 32V16M24 38V10M30 31V17M36 34V14M42 28v-8" />
          </g>
        </svg>
        <h1 class="drop-title">拖入音频，添加任务</h1>
        <p class="drop-sub">{{ formats }}</p>
        <p class="drop-limit mono">{{ limit }}</p>
      </div>
      <span class="drop-scan" aria-hidden="true" />
    </div>

    <p v-if="error" class="error-line" role="alert">{{ error }}</p>
  </div>
</template>
