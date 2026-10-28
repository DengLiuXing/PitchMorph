# 前端实现指南：做出同样手感的四轨工作台

> 目标读者：拿到后端 API、要从零写前端的人。
> 这里只讲**这套交互为什么这么做**，不贴整份组件。核心源码在 `frontend/src/components/Workbench.vue`。

---

## 1. 视觉基线

三件事决定「像不像一个专业工具」，与配色无关：

1. **暗底 + 单一强调色**。底色近黑（`#0a0c10` 一带），强调色只有一个琥珀色，用在「已播放波形 / 选中态 / 主按钮」。第二强调色（青）只用于「已缓存」这类状态标记。
2. **数字一律等宽**。时间码、百分比、GB 数用 `font-variant-numeric: tabular-nums` 的等宽字体，否则播放时数字会左右跳动。
3. **不用圆角卡片堆叠**。层级靠 1px 描边 + 微弱的径向光晕，不靠阴影。

---

## 2. 视图切换：别用 `v-show`

```css
.view { display: none; }
.view.is-active { display: block; }
```

```vue
<section class="view" :class="{ 'is-active': view === 'home' }">
```

**不要**改成 `v-show`。`v-show` 通过移除 inline `display` 来显示，但你的类规则里有 `.view { display: none }`，结果元素依然被藏起来，页面白屏。这个坑踩过一次，症状是「路由变了但整块空白」。

---

## 3. 波形：不要 `decodeAudioData`

### 做法

直接解析 PCM-16 WAV 的字节：

```ts
function wavPeaks(buffer: ArrayBuffer, buckets: number): Float32Array | null {
  const view = new DataView(buffer)
  if (view.getUint32(0, false) !== 0x52494646) return null  // "RIFF"

  let offset = 12, channels = 0, bits = 0, dataStart = 0, dataLength = 0
  while (offset + 8 <= buffer.byteLength) {
    const id = view.getUint32(offset, false)          // "fmt " / "data"
    const size = view.getUint32(offset + 4, true)
    const body = offset + 8
    if (id === 0x666d7420) {
      channels = view.getUint16(body + 2, true)
      bits = view.getUint16(body + 14, true)
    } else if (id === 0x64617461) {
      dataStart = body
      dataLength = Math.min(size, buffer.byteLength - body)
      break
    }
    offset = body + size + (size % 2)
  }
  // 逐 bucket 取 |sample| 的最大值，归一化到 0..1
}
```

### 为什么

`AudioContext.decodeAudioData()` 会额外持有一份 float32 的 `AudioBuffer`：3 分钟立体声 44.1 kHz ≈ **63 MB/轨**，四轨 250 MB，这些数据只为画一张静态图，纯属浪费。PCM-16 的原字节本来就是我们要的峰值来源。

### 分层绘制

每轨两张 canvas 叠放：

```html
<div class="wave" :style="{ '--p': progressPct }">
  <canvas class="wave-base" />   <!-- 灰色底波形 -->
  <canvas class="wave-lit" />    <!-- 琥珀色已播放 -->
</div>
```

```css
.wave-lit { clip-path: inset(0 calc(100% - var(--p)) 0 0); }
```

播放时只改一个 CSS 变量，**不重绘任何像素**。每帧重画两张 canvas 是这类界面最常见的掉帧原因。

`devicePixelRatio` 要处理：`canvas.width = clientWidth * dpr`，再用 `painter.setTransform(dpr,0,0,dpr,0,0)` 让坐标回到 CSS 像素。

---

## 4. 多轨同步播放

### 结构

```
一个 AudioContext
  ├─ MediaElementSource(audio[0]) → GainNode → destination
  ├─ MediaElementSource(audio[1]) → GainNode → destination
  └─ ...
```

每轨一个 `<audio>`（`preload="auto"`），用 `createMediaElementSource` 接进同一个 context。

- 为什么不用 `AudioBufferSourceNode`：后者要整段解码并常驻内存，且每次播放都要重建节点；`<audio>` 天然支持 Range 请求、拖动定位、流式加载。
- 为什么必须有 `GainNode`：静音/独奏/音量要能独立控制，直接改 `audio.volume` 无法做到「独奏时其他轨瞬间静音且音量值保留」。

### 主时钟

```ts
const frame = () => {
  const lead = audios.get(tracks[0].name)!   // 第一轨当主时钟
  const now = lead.currentTime
  for (const track of tracks.slice(1)) {
    const audio = audios.get(track.name)
    if (audio && Math.abs(audio.currentTime - now) > 0.08) audio.currentTime = now
  }
  position.value = now
  raf = requestAnimationFrame(frame)
}
```

- 偏差阈值 **80 ms**：低于这个数听觉上不可分辨，但频繁 `currentTime =` 会造成卡顿，所以只在超过阈值时纠正。
- `position` 用 `ref` 驱动播放头，但**不要**在 rAF 里遍历 DOM 改样式，只改一个 transform。

### AudioContext 的解锁

`AudioContext` 初始是 `suspended`，必须在用户手势（点击播放）里 `await ctx.resume()`，否则静默无声。

### 播放头

```html
<div class="playhead" :style="{ transform: `translateX(${headOffset + ratio * waveWidth}px)` }" />
```

- `headOffset` 是波形区域相对轨道容器的左偏移，`waveWidth` 是它的宽度，都在 `resize` 时用 `getBoundingClientRect()` 重新量。
- 用 `transform` 而不是 `left`：前者走合成层，不触发布局。

---

## 5. 列表状态：服务端是唯一真相

```ts
const jobs = ref<Job[]>([])
async function loadJobs() { jobs.value = await api.jobs() }
```

- 前端**不维护**任务进度的本地状态，只负责渲染服务端返回的快照。
- 轮询只在存在活跃任务时启动，完成后自己停：

```ts
async function poll() {
  await loadJobs()
  if (jobs.value.some(j => ACTIVE.has(j.stage))) startPoll()
}
```

- 轮询间隔 900 ms：低于 500 ms 会让「进度条」看起来更顺，但请求数翻倍；高于 1500 ms 会让取消操作反馈迟钝。

---

## 6. 上传

- 多个文件**串行**上传。服务端本来就会排队，并发上传只会撞 429 限流。
- 上传超时给 180 s（300 MB 在慢盘上要几十秒），普通读接口 15 s。
- 失败信息要能直接展示：后端返回的是中文可读文案（`{ detail: "..." }`），前端原样显示，不要再包一层「请求失败」。

---

## 7. 音高滑动窗口（新增功能的画法）

前提：后端一次性返回整首的 f0 数组（10 ms 一帧），前端在内存里切片。**滑动时不发请求。**

### 纵轴：对数映射

```ts
const BOTTOM_HZ = 220    // A3
const OCTAVES = 2        // 顶部 880 Hz

// f → y（y=0 在顶部）
const y = height * (1 - Math.log(f / BOTTOM_HZ) / (Math.log(2) * OCTAVES))
```

线性映射会让低音区被压扁：220–440 与 440–880 在听觉上是等距的两个八度，必须在屏幕上等距。

### 横轴：跟随播放头

```ts
const half = 5 * 1000 / 10            // 5 秒 / 10 ms = 500 帧
const from = Math.max(0, index - half)
const slice = f0.slice(from, from + half * 2)
```

超出首尾的区段留白，不要把数组硬拉满整宽——那会让波形与音高图的时间轴对不上。

### 无声帧

后端用 `null` 表示无声（静音、清辅音、气声）。绘制时：

```ts
if (f === null) { path.moveTo(nextX(), 0); continue }  // 断开，不要 lineTo
path.lineTo(x, y(f))
```

如果把 `null` 当 0 Hz 处理，会画出一条贴着 A3 底边的假线，看起来像「一直在唱低音」。

### 网格

- 实线：A3 / A4 / A5（八度边界）
- 虚线：C4 / C5（人声换声区参考）

### 性能

播放时每帧要做的事：`slice`（Array.slice，500 元素）+ `clearRect` + 一条 `stroke`。这远低于一帧预算，不需要节流。如果数据量再大，改成 `Float32Array.subarray` 视图而不是 `slice` 拷贝。

---

## 8. 类型与构建

- 所有 API 形状集中在 `src/api/types.ts`，注释写明「与 `app/schemas.py` 保持同步」。后端加字段时先改这里，`vue-tsc --noEmit` 会把漏改的地方指出来。
- 构建产物写入 `app/static`（`vite.config.ts` 里 `base: '/static/'`），该目录由构建清空重写，**不要手改**。
- 开发时 `/api` 与 `/media` 代理到 `localhost:8000`，见 `vite.config.ts`。

---

## 9. 复用清单

如果要把这套交互搬到新项目（比如 PyTorch 后端），可以直接照搬的部分：

| 模块 | 可复用性 | 注意 |
|---|---|---|
| 波形解析与绘制 | **完全可复用** | 只依赖 PCM-16 WAV 格式，与后端无关 |
| 多轨同步播放 | **完全可复用** | 只依赖 `<audio>` 与静态文件 URL |
| 播放头 / 进度条 | **完全可复用** | 依赖 `duration` 与 `position` |
| 任务列表与轮询 | 需小改 | 后端任务字段若变化，改 `types.ts` |
| 音高窗口 | **完全可复用** | 只要后端给 `{hop_sec, f0[]}` |
| 上传表单 | 需小改 | 后端若换成 WebSocket 推送，轮询可去掉 |
