<script setup lang="ts">
import { UploadCloud } from 'lucide-vue-next'
import { ref } from 'vue'

const props = defineProps<{ busy: boolean }>()
const emit = defineEmits<{ (event: 'pick', files: File[]): void }>()

const ACCEPT = '.wav,.flac,.ogg,.mp3,.m4a,.aac,.mp4,.opus,.aiff,.aif'

const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

function take(list: FileList | null): void {
  if (props.busy || !list || list.length === 0) return
  emit('pick', [...list])
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  take(event.dataTransfer?.files ?? null)
}

function onInput(event: Event): void {
  const element = event.target as HTMLInputElement
  take(element.files)
  element.value = '' // 允许连续选同一个文件
}

function open(): void {
  if (!props.busy) input.value?.click()
}
</script>

<template>
  <button
    type="button"
    class="md-upload"
    :data-drag="dragging ? 'true' : 'false'"
    :data-busy="busy ? 'true' : 'false'"
    :aria-busy="busy"
    @click="open()"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <span class="md-upload-icon">
      <UploadCloud :size="22" />
    </span>

    <span class="md-title-medium">{{ busy ? '正在上传…' : '拖入音频，或点击选择文件' }}</span>
    <span class="md-label-medium md-num text-on-surface-variant">
      WAV / FLAC / MP3 / M4A / OGG · 单文件 ≤ 200 MB · 时长 ≤ 15 min
    </span>

    <input ref="input" type="file" multiple :accept="ACCEPT" class="hidden" @change="onInput" />
  </button>
</template>
