import { onUnmounted, ref, shallowRef } from 'vue'

interface TrackNode {
  audio: HTMLAudioElement
  gain: GainNode
}

// 120 ms 是能听出「错拍」的门槛；设得太小，每次微调 currentTime 都会让浏览器
// 重发 Range 请求，反倒拖出音频卡顿
const DRIFT_LIMIT = 0.12

/**
 * 多轨同步播放。
 *
 * 每轨一个 <audio>（自带 Range 请求，拖动定位便宜），经 MediaElementSource 进同一个
 * AudioContext，由各自 GainNode 控制音量/静音/独奏。
 *
 * 主时钟是**第一条注册的轨**而不是写死的名字：「仅人声」档只有 vocals.wav。
 * 播放位置走「墙钟外推 + 低通收敛」，不直接用 element.currentTime——
 * 后者是量化跳变的，直接拿来当播放头会一卡一卡。
 */
export function usePlayer() {
  const context = shallowRef<AudioContext | null>(null)
  const nodes = new Map<string, TrackNode>()
  const masterName = ref<string | null>(null)

  const playing = ref(false)
  const position = ref(0)
  const duration = ref(0)
  const volumes = ref<Record<string, number>>({})
  const muted = ref<string[]>([])
  const solo = ref<string[]>([])

  let raf = 0
  let clockOffset = 0
  let clockReady = false

  function resync(seconds: number): void {
    clockOffset = seconds - performance.now() / 1000
    clockReady = true
    position.value = seconds
  }

  function ensureContext(): AudioContext {
    if (!context.value) context.value = new AudioContext()
    return context.value
  }

  function master(): TrackNode | undefined {
    return masterName.value ? nodes.get(masterName.value) : undefined
  }

  function applyGains(): void {
    const soloing = solo.value.length > 0
    for (const [name, node] of nodes) {
      const silenced = soloing ? !solo.value.includes(name) : muted.value.includes(name)
      node.gain.gain.value = silenced ? 0 : (volumes.value[name] ?? 0.9)
    }
  }

  function tick(): void {
    const lead = master()
    if (lead) {
      const audioNow = lead.audio.currentTime
      const wall = performance.now() / 1000

      if (lead.audio.paused || !clockReady) {
        resync(audioNow)
      } else {
        const drift = audioNow - (wall + clockOffset)
        if (Math.abs(drift) > 0.25) {
          resync(audioNow) // 真跳了（seek / 缓冲卡顿）就别慢慢追
        } else {
          clockOffset += drift * 0.12
          position.value = Math.max(0, wall + clockOffset)
        }
      }

      // 对齐用真实音频时间，不用外推值
      for (const [name, node] of nodes) {
        if (name === masterName.value) continue
        if (Math.abs(node.audio.currentTime - audioNow) > DRIFT_LIMIT) node.audio.currentTime = audioNow
      }
    }
    raf = requestAnimationFrame(tick)
  }

  /** 注册一条轨的 audio 元素。必须先于 play() 调用 */
  function attach(name: string, element: HTMLAudioElement): void {
    if (nodes.has(name)) return
    const ctx = ensureContext()
    const gain = ctx.createGain()
    ctx.createMediaElementSource(element).connect(gain)
    gain.connect(ctx.destination)
    nodes.set(name, { audio: element, gain })

    if (volumes.value[name] === undefined) volumes.value = { ...volumes.value, [name]: 0.9 }
    if (!masterName.value) {
      masterName.value = name
      const sync = () => {
        if (!duration.value) duration.value = element.duration || 0
      }
      element.addEventListener('loadedmetadata', sync)
      if (element.readyState >= 1) sync()
    }
    applyGains()
  }

  /** 后端 meta 里有精确时长，先垫上，别等 loadedmetadata */
  function setDuration(seconds: number): void {
    if (seconds > 0) duration.value = seconds
  }

  async function play(): Promise<void> {
    const lead = master()
    if (!lead) return
    const ctx = ensureContext()
    // AudioContext 出生就是 suspended，必须在用户手势里 resume，否则静音
    if (ctx.state === 'suspended') await ctx.resume()

    const at = lead.audio.currentTime
    const all = [...nodes.values()]
    for (const node of all) node.audio.currentTime = at
    await Promise.all(all.map((node) => node.audio.play().catch(() => undefined)))
    playing.value = true
    cancelAnimationFrame(raf)
    resync(lead.audio.currentTime)
    raf = requestAnimationFrame(tick)
  }

  function pause(): void {
    for (const node of nodes.values()) node.audio.pause()
    playing.value = false
    cancelAnimationFrame(raf)
  }

  function toggle(): void {
    if (playing.value) pause()
    else void play()
  }

  function seek(seconds: number): void {
    const limit = duration.value
    const clamped = limit > 0 ? Math.min(Math.max(0, seconds), limit) : Math.max(0, seconds)
    for (const node of nodes.values()) node.audio.currentTime = clamped
    resync(clamped)
  }

  function nudge(delta: number): void {
    seek(position.value + delta)
  }

  function toggleMute(name: string): void {
    muted.value = muted.value.includes(name)
      ? muted.value.filter((item) => item !== name)
      : [...muted.value, name]
    applyGains()
  }

  function toggleSolo(name: string): void {
    solo.value = solo.value.includes(name)
      ? solo.value.filter((item) => item !== name)
      : [...solo.value, name]
    applyGains()
  }

  function setVolume(name: string, value: number): void {
    volumes.value = { ...volumes.value, [name]: Math.min(1, Math.max(0, value)) }
    applyGains()
  }

  /** 被 solo 挤掉或被静音：整轨在视觉上退到背景 */
  function isSilent(name: string): boolean {
    return solo.value.length > 0 ? !solo.value.includes(name) : muted.value.includes(name)
  }

  /** 轨从界面上消失（如提纯后原轨被派生轨代替）：从播放器里摘掉，别让它在后台出声 */
  function detach(name: string): void {
    const node = nodes.get(name)
    if (!node) return
    node.audio.pause()
    node.gain.disconnect()
    nodes.delete(name)
    if (masterName.value === name) {
      masterName.value = [...nodes.keys()][0] ?? null
      clockReady = false
    }
  }

  function reset(): void {
    pause()
    clockReady = false
    for (const node of nodes.values()) {
      node.audio.pause()
      node.audio.removeAttribute('src')
    }
    nodes.clear()
    masterName.value = null
    position.value = 0
    duration.value = 0
    volumes.value = {}
    muted.value = []
    solo.value = []
    void context.value?.close()
    context.value = null
  }

  onUnmounted(() => {
    cancelAnimationFrame(raf)
    void context.value?.close()
  })

  return {
    playing,
    position,
    duration,
    volumes,
    muted,
    solo,
    attach,
    setDuration,
    play,
    pause,
    toggle,
    seek,
    nudge,
    toggleMute,
    toggleSolo,
    setVolume,
    isSilent,
    detach,
    reset,
  }
}
