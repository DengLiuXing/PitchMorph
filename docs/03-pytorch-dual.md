# 下一阶段设计：PyTorch 双重心工具（分轨 + 音高 → MIDI）

> 这是一份**待验证的设计**，不是已实现的说明。所有标了「待测」的数字都需要自己跑一遍。
> 前一个项目（ONNX 单轨分离）已挂起，它的价值是「哪些坑已经踩完」，不是「代码要照抄」。

---

## 1. 为什么要换掉 ONNX

上一版选 ONNX 是为了「不装 PyTorch、包体小」。当需求扩到「音高分析 + 导出 MIDI」之后，这个选择开始反向收费：

| 需求 | ONNX 路线的处境 |
|---|---|
| 换/微调分离模型 | 要重新导出，输入形状锁死 `(1,2,343980)` |
| 音高估计 | FCPE 官方只有 PyTorch checkpoint，仓库与 HF 上都没有 ONNX |
| f0 → 音符 → MIDI | 需要精细控制帧级后处理，纯 numpy 能做，但调试成本高 |
| 批量/多模型共存 | 每个 ONNX session 独占一份 CUDA arena，N 个模型 = N 份显存 |

结论：**新项目直接上 PyTorch**，把「模型可换」放在第一位。上一个项目的 ONNX 代码作为参考实现保留，不再演进。

顺带解决一个部署问题：PyTorch 的 wheel **自带** CUDA 运行时（`torch/lib/` 下有 `cudart64_*.dll`、`cublas`、`cudnn`），所以 Windows 上 `uv add torch --index-url .../cu126` 之后**不需要再装 CUDA Toolkit、不需要配 PATH、不需要管 DLL 版本**。上一个项目为此付出的代价是「五个组件缺一不可 + 手动注册 DLL 目录 + 版本 pin」，换栈后整体消失。

代价是包体从 160 MB 变成 2.3 GB。这正是 `01-decisions.md` 里 D2 的教训：**包体可以妥协，可维护性不能**。

---

## 2. 三个能力，三块模型

### 2.1 声轨分离

| 选项 | 说明 |
|---|---|
| `demucs` 官方 `htdemucs_ft` | 4 个专家，质量最好；单次约 4× 单模型算力 |
| `htdemucs` | 单文件出 4 轨，快，质量略低 |
| `htdemucs_6s` | 6 轨（多 guitar / piano） |

建议沿用上一版的档位设计（`vocals` 单专家 / `htdemucs` 快速 / `htdemucs_ft` 质量），因为「只想抽人声」是最高频的用法。

**待测**：RTX 5060 Laptop 上 `htdemucs_ft` 的 RTF。ONNX 版实测单块 0.70 s / 7.8 s 音频（RTF 0.09），PyTorch 版通常更快（无 ONNX 图开销），但显存占用更高。

### 2.2 音高估计（FCPE）

```python
from torchfcpe import spawn_bundled_infer_model
model = spawn_bundled_infer_model(device="cuda")
f0 = model.infer(audio_16k, sr=16000, hop_size=160, f0_min=80, f0_max=1100)
```

- 输入：16 kHz 单声道；hop 160 = **10 ms 一帧**。
- 输出：每帧的 f0（Hz），无声帧为 0 或 nan，需要显式转成「无声」标记。

**待测**：3 分钟人声的推理耗时与显存峰值。

### 2.3 f0 → MIDI

这一步没有现成「一键」方案，必须自己写后处理。流水线：

```
f0[] (10 ms/帧)
  → 1. 去除无声帧（阈值 + 能量门限）
  → 2. 中值滤波（去掉孤立的八度跳变）
  → 3. 音符切分（连续同音高超过 N 帧才算一个音符）
  → 4. 量化到节拍（可选，取决于要不要对齐 BPM）
  → 5. 写 MIDI（pretty_midi 或 mido）
```

三个关键参数，都需要试听调：

| 参数 | 起始值 | 作用 |
|---|---|---|
| 最短音符 | 60 ms | 低于这个时长的碎片不生成音符 |
| 音高容差 | ±50 cents | 超过才算「换音」 |
| 静音门限 | -40 dBFS | 低于此视为无声，避免气声被识别成音符 |

**不要**在第一步就做量化：没有明确 BPM 的素材量化后会更难听。先导出「原始时值」的 MIDI，确认音高对了，再考虑量化。

---

## 3. 进程模型：推理放子进程

这是与上一版最大的架构差异。

### 上一版（ONNX）

线程池 + 单 worker + 进程内会话缓存。可行，因为 ORT 的 session 在 Python 线程间是安全的，且显存可以靠 `release_sessions()` 归还。

### 新项目（PyTorch）

**建议**：每个推理任务跑在一个独立子进程里。

理由：

1. **显存归还**。`torch.cuda.empty_cache()` 只能回收缓存分配器里未使用的块，被 Python 对象持有的张量仍然占着。子进程退出是唯一确定性的释放方式。
2. **崩溃隔离**。CUDA OOM 在 PyTorch 里常常让 context 处于不可用状态，后续调用全部失败。子进程崩了，主进程重启一个即可。
3. **模型切换**。分离模型和音高模型可以分别加载在不同子进程，不会互相挤显存。

代价：进度与取消要跨进程传递。做法是子进程通过 `stdout` 输出结构化行（JSON），主进程读取并更新任务状态；取消用 `terminate()`（可接受，因为结果是幂等重建的）。

```python
# 子进程协议（stdout 每行一个 JSON）
{"stage": "loading",       "progress": 0.00}
{"stage": "separating",    "progress": 0.42, "stem": "vocals"}
{"stage": "done",          "stems": ["drums.wav", ...]}
{"stage": "error",         "message": "CUDA out of memory"}
```

主进程只读这些行，不解析模型的任何内部状态——这条边界让「换模型」不影响任务层。

---

## 4. 磁盘契约

沿用上一版的布局，只是多一层音高产物：

```text
data/jobs/{id}/
├── input.wav              # 统一为 44.1k 立体声 PCM16
├── stems/
│   ├── drums.wav
│   ├── bass.wav
│   ├── other.wav
│   └── vocals.wav
├── features/
│   ├── pitch.json         # {version, model, sample_rate, hop_sec, frames, f0:[float|null]}
│   └── notes.json         # 音符切分结果（可选，便于前端画块）
└── exports/
    └── vocals.mid         # 由 notes 生成
```

三条规则：

- `stems/` 与 `features/` **平级**，因为音高依赖的是 `vocals.wav` 的稳定路径。
- `features/pitch.json` 用 `null` 表示无声，不要用 0（0 Hz 不合法，前端会当成有效的低音）。
- `exports/` 是派生产物，可随时删了重建，不参与缓存判定。

---

## 5. 分阶段计划（每阶段都能验收）

### 阶段 1 · 跑通分离（1 天）

- `uv add torch torchaudio demucs`
- 用官方 `demucs.api.Separator` 跑一首歌，确认四轨产出
- **验收**：与上一版 ONNX 的 `vocals.wav` 逐采样比对，max abs diff 应在 1e-3 内（同源模型，差异来自数值库）
- **记录**：RTF、显存峰值、首块延迟

### 阶段 2 · 跑通音高（半天）

- `uv add torchfcpe`
- 对阶段 1 的 `vocals.wav` 跑 FCPE，输出 `pitch.json`
- **验收**：用 440 Hz 正弦测，全段应在 440±2 Hz；静音段全 `null`

### 阶段 3 · 接上现有的 Web 层（1 天）

可以直接照搬上一版的：解码层（soundfile → ffmpeg）、任务表、`/media` 静态托管、前端工作台。
只需要把「推理」替换成子进程调用。

- **验收**：浏览器上传 → 分轨 → 播放，与上一版体验一致

### 阶段 4 · 音高滑动窗口（1 天）

- 后端：`GET/POST /api/jobs/{id}/pitch`
- 前端：`02-frontend.md` 第 7 节

### 阶段 5 · MIDI 导出（2 天，最难）

- 写音符切分（第 2.3 节的五步流水线）
- 用 `pretty_midi` 写 `.mid`
- **验收**：在 DAW 里打开，音高正确；连续音高变化被正确切成多个音符；静音段没有音符

---

## 6. 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| torch 包体 2.5 GB+ | 装机体验差 | 用 `uv` 的 CPU/GPU 索引分离，文档写清两套命令 |
| CUDA 版本与 torch 绑定 | 换机器容易装错 | 固定 `torch==2.x.y+cu126`，不要用 `latest` |
| 显存不够同时加载分离+音高模型 | OOM | 子进程串行，一次只加载一个模型 |
| FCPE 输出抖动 | MIDI 音符碎片化 | 中值滤波 + 最短音符阈值；参数化，允许调 |
| 没有 BPM 的素材量化后更难听 | 结果不可用 | 默认不量化，提供开关 |

---

## 7. 能从上个版本直接拿走的资产

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 前端整套交互 | `docs/02-frontend.md` | 直接照搬，只改 `types.ts` |
| 解码与容器探测 | `app/media.py` | 原样复制（只依赖 soundfile + ffmpeg） |
| 任务表与 TTL 清理 | `app/jobs.py` | 原样复制，把推理调用换成子进程 |
| 显存与并发的实测结论 | `docs/perf-audit.md` | **只作参考**：ORT 的 arena 问题在 PyTorch 下不存在，但「并发在单卡上是负优化」的结论仍然成立 |
| 分块与 overlap-add | `app/separator.py` | 不需要：demucs 官方 API 内部已处理 |

---

## 8. 一句话总结

**把「模型」和「任务」之间的边界画清楚**：模型在子进程里，通过 stdout 说人话（JSON）；任务层只管状态与文件。这样换模型、加能力、甚至换语言，都不用动 Web 层。
