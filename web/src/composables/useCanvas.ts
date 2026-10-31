import { onBeforeUnmount, ref } from 'vue'

import { prepare, type Size } from '@/lib/canvas'

/**
 * 一个 canvas + 一处绘制入口：**同步画**，不套 rAF。
 *
 * 之前踩过的坑：在 paint() 里 cancelAnimationFrame 再重排下一帧，播放时会被
 * 播放器的 rAF 每帧取消一次，回调永远排不到——表现为「暂停才刷新、播放时冻住」。
 * 只合并又会隔帧才画一次（30 fps）。watch 本身已按帧批处理，直接画即可。
 */
export function useCanvas(draw: (ctx: CanvasRenderingContext2D, size: Size) => void) {
  const canvas = ref<HTMLCanvasElement | null>(null)

  function paint(): void {
    const element = canvas.value
    if (!element) return
    const ready = prepare(element)
    if (ready) draw(ready.ctx, ready.size)
  }

  onBeforeUnmount(() => {
    canvas.value = null
  })

  return { canvas, paint }
}
