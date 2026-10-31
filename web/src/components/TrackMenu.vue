<script setup lang="ts">
import { Activity, FolderDown, Music2, Sparkles } from 'lucide-vue-next'

import MdMenu from '@/components/MdMenu.vue'

defineProps<{ x: number; y: number; pitchOn: boolean }>()
const emit = defineEmits<{ (event: 'pitch'): void; (event: 'close'): void }>()
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

    <button type="button" class="md-menu-item" role="menuitem" disabled title="下一轮">
      <Sparkles :size="18" />
      人声提纯
      <span class="md-menu-flag">soon</span>
    </button>
    <button type="button" class="md-menu-item" role="menuitem" disabled title="下一轮">
      <Music2 :size="18" />
      提取音符
      <span class="md-menu-flag">soon</span>
    </button>
    <button type="button" class="md-menu-item" role="menuitem" disabled title="下一轮">
      <FolderDown :size="18" />
      导出工程
      <span class="md-menu-flag">soon</span>
    </button>
  </MdMenu>
</template>
