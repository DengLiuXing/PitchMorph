<script setup lang="ts">
import { AudioLines, UploadCloud } from 'lucide-vue-next'
import { ref } from 'vue'

const emit = defineEmits<{ (event: 'pick', files: File[]): void }>()

const dragging = ref(false)
const busy = ref(false)

function submit(files: FileList | null): void {
  if (!files || files.length === 0) return
  emit('pick', Array.from(files))
  busy.value = true
  window.setTimeout(() => (busy.value = false), 600)
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  submit(event.dataTransfer?.files ?? null)
}

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  submit(input.files)
  input.value = ''
}
</script>

<template>
  <label
    class="dropzone"
    :class="{ 'is-drag': dragging }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <input
      type="file"
      multiple
      accept="audio/*,video/*,.m4s,.m4a,.flac,.wav,.mp3,.mp4,.mkv,.ts"
      class="sr-only"
      @change="onChange"
    />

    <span class="drop-icon">
      <UploadCloud v-if="!busy" :size="19" />
      <AudioLines v-else :size="19" class="animate-pulse" />
    </span>

    <div class="flex flex-col items-center gap-1">
      <span class="text-[14px] font-medium text-chalk">
        {{ busy ? '正在上传…' : '把整首歌拖进来' }}
      </span>
      <span class="max-w-md text-[12.5px] leading-relaxed text-chalk-dim">
        支持 mp3 / flac / wav / mp4 / m4s 等格式，容器类只取第一条音轨。
        音频不出本机，四轨、音高线与 MIDI 都在本地算完。
      </span>
    </div>
  </label>
</template>
