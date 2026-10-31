<script setup lang="ts">
import { Activity, FileAudio, FolderDown, Music2, Sparkles } from 'lucide-vue-next'

import MdMenu from '@/components/MdMenu.vue'

defineProps<{
  x: number
  y: number
  pitchOn: boolean
  /** 提纯只对 vocals 与它的派生轨有意义 */
  canRefine: boolean
  /** 音符提取需要这条轨已经分好 */
  canNotes: boolean
  /** 导出要等音符算完 */
  notesReady: boolean
}>()
const emit = defineEmits<{
  (event: 'pitch'): void
  (event: 'refine'): void
  (event: 'notes'): void
  (event: 'export', fmt: 'ustx' | 'mid'): void
  (event: 'close'): void
}>()
</script>

<template>
  <MdMenu :x="x" :y="y" @close="emit('close')">
    <button
      type="button"
      class="md-menu-item"
      role="menuitem"
      :aria-pressed="pitchOn"
      @click="emit('pitch')"
    >
      <Activity :size="18" />
      音高线
      <span v-if="pitchOn" class="md-menu-flag">显示中</span>
    </button>

    <div class="md-menu-sep" />

    <button
      type="button"
      class="md-menu-item"
      role="menuitem"
      :disabled="!canRefine"
      :title="canRefine ? '' : '只对人声轨可用'"
      @click="emit('refine')"
    >
      <Sparkles :size="18" />
      人声提纯
      <span class="md-menu-flag">lead + backing</span>
    </button>
    <button
      type="button"
      class="md-menu-item"
      role="menuitem"
      :disabled="!canNotes"
      @click="emit('notes')"
    >
      <Music2 :size="18" />
      提取音符
    </button>
    <button
      type="button"
      class="md-menu-item"
      role="menuitem"
      :disabled="!notesReady"
      @click="emit('export', 'ustx')"
    >
      <FolderDown :size="18" />
      导出 OpenUtau 工程
    </button>
    <button
      type="button"
      class="md-menu-item"
      role="menuitem"
      :disabled="!notesReady"
      @click="emit('export', 'mid')"
    >
      <FileAudio :size="18" />
      导出 MIDI
    </button>
  </MdMenu>
</template>
