# 性能档案：分轨为什么慢，以及 GPU 与并行的取舍

> ⚠️ **本文件全部数据来自 v1（ONNX Runtime + demucs-onnx），PitchMorph v2 已换成 PyTorch。**
> 换栈后以下结论**不再适用**：arena 只增不减、`enable_mem_pattern`、`arena_extend_strategy`、
> `cudnn_conv_algo_search`、会话常驻与 `release_sessions()`、显存 3.2 GB 的台阶、
> `intra_op_num_threads` 扫描、92 字节 Identity 图探针。PyTorch 自带 CUDA 运行时，这一整套问题域消失。
>
> **仍然成立的两条**：① 单卡上多专家并发是负优化（吞吐不变、显存翻倍）；
> ② 「95% 时间在逐块推理上，想快只能动推理」。v2 的实测数字见 `AGENTS.md` 的「当前阶段」。

> 结论会变，数据不会。当前生效的结论在「现状」，实测数据按主题归在「数据」，
> 试过并已否决的方向在「别再试」，还没做的在「待办」。动推理或并发相关代码前读这份，
> 别把已经量过的假设重新量一遍。

## 现状（2026-10-28）

1. 一次分轨 **95.4%** 的时间在逐块推理上，解码 / 加载 / 写盘合计 < 5%——想快只能动推理。
2. 默认档是「仅人声」（`config.DEFAULT_TIER = "vocals"`）：只跑一个专家，工作量是四轨档的四分之一。`/api/models` 把默认档排在第一位，否则前端会选中列表首项。
3. 默认并行度 1（`PITCHMORPH_PARALLEL`，上限 `config.MAX_PARALLEL` = 4）。并发 2 在 CPU 和 GPU 上都是**负优化**，见「并发实测」。
4. CUDA 已落地并默认启用：启动时用 92 字节的 Identity 图真探针一次，可用走 GPU，否则回落 CPU。稳态单块 **0.70 s**（CPU 1.41 s，约 2×）。
5. 模型会话**不常驻**：每批跑完即 `sessions.clear()` + `runtime.release_sessions()`，重建约 4 s/专家。这是 8 GB 笔记本显卡上不 OOM 的前提。
6. 数值安全：`_chunked_infer` 与上游 `demucs_onnx.separate()` 逐采样一致（max abs diff = 0）；`parallel=1 / 2` 与改造前的串行实现之间，四轨差异同样是 0。

## 测量环境

| 项 | 值 |
|---|---|
| CPU | Intel i7-14650HX，16 核 / 24 线程 |
| GPU | RTX 5060 Laptop，8 GB（8151 MiB），compute_cap **12.0（sm_120 / Blackwell）** |
| 驱动 | 617.42，CUDA UMD **13.4**；系统 CUDA Toolkit 13.4（DLL 在 `bin\x64`） |
| ONNX Runtime | `onnxruntime-gpu` 1.31.1 + `nvidia-cudnn-cu13` 9.27（10-27 的 CPU 基线是 `onnxruntime` 1.31.1 CPU wheel） |
| 电源方案 | **平衡**（`powercfg /getactivescheme`，未改，见「待办」第一条） |
| 模型 | `htdemucs_ft` 四专家，fp16weights，单份 157.9 MB |
| 素材 | `samples/物凄.m4s`（12 s）、`samples/1333699443-1-30280.m4s`（398.8 s，纯音轨）、现场生成的 60 s 片段。当时那份 398.9 s 素材叫 `samples/美丽之物.mp3`，已不在盘上；下表里的「整曲」指同时长的这轨 |

## 数据

### 耗时构成（CPU，quality 档，398.9 s 整曲，插桩计时）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 解码 mp3 → WAV | 0.60 s | 0.1% |
| 模型加载 ×4 | 19.78 s | 3.9% |
| **逐块推理（276 次 `session.run`）** | **488.6 s** | **95.4%** |
| 写盘 ×4 | 1.22 s | 0.2% |
| **合计** | **512.1 s（8 分 32 秒）** | overall RTF **1.28** |

单块中位数 **1.722 s**（块长 7.8 s，单专家 RTF ≈ 0.22）。四次 session 加载分别为 3.99 / 7.58 / 4.15 / 4.06 s。

**为什么是 276 次**：四个专家各跑一遍整曲。图输出固定为 `(1, 4, 2, N)`，无法只算一行（上游 `inference.py` 明确 "no per-stem-skip"），69 块 × 4 专家 = 276 次整图推理。所以「htdemucs ~30% faster than FT bag」这个上游说法在 ONNX 场景不成立——这里是四张独立图逐张跑，接近 4×。

### 单块微基准与长跑退化（CPU）

| 指标 | 值 |
|---|---|
| 首次 run | 1.42 s |
| 稳态 5 次中位数 | **1.21 s** |
| RTF（单专家） | 0.16 |

同一块音频，短基准 1.21 s、长跑稳态 1.72 s，**差 42%**。短基准测到的是 boost 峰值，规划时必须用长跑数字。

### 线程数扫描（`intra_op_num_threads`，同一块输入）

| 设置 | 中位数 | RTF |
|---|---|---|
| 默认（0） | **1.19 s** | 0.15 |
| 24 | 1.80 s | 0.23 |
| 16 | 1.35 s | 0.17 |
| 8 | 1.87 s | 0.24 |
| 4 | 2.00 s | 0.26 |

**默认已最优，线程调优无收益**；超订（24 线程）反而慢 51%。单 session 就吃满了物理核，所以「更快」只能来自另一块设备或另一个 session。

### CUDA 收益

首次实测（60 s 素材，vocals 档，11 个分块，修复显存前）：

| | CPU | GPU | 倍数 |
|---|---|---|---|
| 单块 | 1.227 s | **0.702 s** | 1.75× |
| 推理总耗时 | 13.9 s | 8.4 s | 1.65× |
| 全流程 | 17.9 s | 12.6 s | 1.42× |

显存选项调好之后同条件重测（同一段 60 s 素材）：

| | CPU | GPU | 倍数 |
|---|---|---|---|
| 单块中位 | 1.412 s | 0.704 s | **2.01×** |
| 推理（11 块） | 15.7 s | 8.2 s | 1.91× |
| 全流程 | 22.9 s | 12.5 s | 1.83× |

两次差异在温度 / 电源漂移范围内，关键是**显存优化没有牺牲速度**。

**首次运行有 CUDA 图编译**：8 秒素材（2 块）首测 3.78 s/块，第二次才回到稳态。短样本测不出真实收益，至少要跑到 10 个块。

**加速只有 2× 左右，并非预期的 3–6×**，可能原因（未逐项验证）：`batch=1` + 定长 7.8 s 输入让 kernel launch 与 H2D/D2H 占比过高；模型仅 79 M 参数，单块算力喂不满 GPU；电源方案仍是「平衡」，CPU/GPU 都降频。结论上仍然采纳：GPU 是当时唯一稳定有效的方向，CPU 并行是反向优化。

### 端到端（`scripts/bench.py`，含显存采样）

| 场景 | 加载 | 推理 | 单块中位 | 显存峰值 | 全流程 |
|---|---|---|---|---|---|
| vocals / 12 s | 4.34 s | 2.7 s | 0.752 s | 3309 MiB | 7.3 s |
| vocals / 60 s | 3.97 s | 8.2 s | 0.704 s | 3309 MiB | 12.5 s |
| vocals / 398.9 s（整首，69 块） | 3.95 s | 49.5 s | 0.715 s | 3309 MiB | 54.7 s（RTF 0.14） |
| quality / 60 s（四专家） | 16.16 s | 31.9 s | 0.716 s | 3309 MiB | 48.8 s（RTF 0.81） |

quality 档峰值与单专家**相同**，说明每批释放真的把显存还回去了（修复前同一场景 7836 MiB 且 OOM）。

### 显存：为什么会 OOM，以及现在的做法

一次 `quality` 任务在 RTX 5060 Laptop（**8151 MiB**）上崩过：

```
CUDA failure 2: out of memory
expr=cudaMemcpy(dst_data, src_data, bytes, cudaMemcpyHostToDevice)
```

失败点是把 **2.7 MB 的输入拷进显存**——设备已经满了，不是这次推理吃得多。随后一串 `cudnnDestroy ... CUDNN_STATUS_INTERNAL_ERROR` 是坏掉的 context 在退出时的次生现象。

逐个加载四个专家、各跑一个真实分块：

| 状态 | 显存 |
|---|---|
| 空载 | 96 MiB |
| + drums | 3308 MiB |
| + bass | 6534 MiB |
| + other | 7814 MiB |
| + vocals | 7836 MiB |

**一个专家跑完一个分块要占 2.2–3.2 GB**，而刚建好时只有 ~700 MiB：CUDA arena 按 `kNextPowerOfTwo` 增长，跑完不回缩。原来「四个专家全常驻」是 CPU 时代的假设——CPU 上只花内存、省 4 s 加载；到 GPU 上四个专家 = 7.8 GB，加桌面占用必然 OOM。

现在每批结束即丢弃（`finally` 里还有一次兜底）。顺序不能反：先 `sessions.clear()` 丢掉局部字典的引用，再 `runtime.release_sessions()` 清缓存，否则 session 会被字典吊住、显存不还。实测重建成本：

```
round 0: build 3.9s  first chunk 1.2s
round 1: build 4.0s  first chunk 0.7s
round 2: build 4.1s  first chunk 0.7s
```

约 **4 s/专家**（CUDA 图编译为主，与磁盘缓存无关），`quality` 档四专家合计 16 s——远小于一次失败重来的代价。

生效的 CUDA 选项（`app/runtime.py`，可用 `PITCHMORPH_CUDA_OPTS` 覆盖前两项）：

| 选项 | 值 | 作用 |
|---|---|---|
| `arena_extend_strategy` | `kSameAsRequested` | arena 按实际申请量扩张，不再按 2 的幂预留 |
| `cudnn_conv_algo_search` | `HEURISTIC` | 跳过逐形状 benchmark，不保留试探过程中的最大 workspace |
| `enable_mem_pattern`（SessionOptions） | `False` | 关掉按形状的中间张量预分配，**降幅最大的一项** |

逐步显存（vocals 专家，单 session）：

| 阶段 | 修复前 | 修复后 |
|---|---|---|
| 空载 build | 704 MiB | 615 MiB |
| 跑完第 1 块 | 3245 MiB | 3245 MiB |
| 跑完第 2 块 | **5675 MiB** | **3245 MiB** |
| 跑完第 3 块 | 5675 MiB | 3245 MiB |
| `release_sessions()` 后 | 195 MiB | 195 MiB |

第 2 块那 2.4 GB 的台阶来自 memory pattern：形状固定时它为每个中间张量各留一块，生命周期不重叠的也不复用。关掉后峰值从第一块起就是稳态，单块耗时没有变化（0.702 → 0.704 s）。

### 并发实测：两个专家不会更快

CPU（quality 档，整曲 398.9 s）：

| 场景 | parallel=1 | parallel=2 | 结论 |
|---|---|---|---|
| 60 s 片段 | **85.4 s** | **143.4 s** | 慢 68% |
| 398.9 s 全曲 | **520.0 s** | 未跑完：批 2 中途进程被系统终止，WorkingSet 10.2 GB | — |

- 短样本并行时 WorkingSet 峰值约 **9.7 GB**。「批大小 × 0.6 GB」的估算完全不成立——ORT 的 CPU arena 随线程数膨胀。
- 全曲 parallel=1 比 10-27 的 512.1 s 慢 1.5%，属于温度 / 电源漂移。

GPU（`samples/物凄.m4s`，12 s，quality 档）：

| | parallel=1 | parallel=2 |
|---|---|---|
| 单块中位 | 0.716 s（60 s 素材，44 次 run） | **1.381 s**（12 s 素材，12 次 run） |
| 显存峰值 | 3309 MiB | **6330 MiB** |
| 单专家 3 块的推理 | 约 2.1 s | 约 4.1 s |

两个专家抢同一块 GPU，单块耗时正好翻倍，总吞吐不变而显存翻倍。**具体倍数随机型、驱动、显卡档位变化，别当普适规律，也别写进 UI**；界面只讲「一个专家就能跑满 GPU 算力，建议保持 1」。

所以并发开关的定位是「让用户自己验证」，不是提速手段：默认 1，前端控件标注实验性，`POST /api/jobs` 原样接受 1–4，单轨档与单文件档没有可并行的对象、控件置灰。

### 归因：库特性 vs 程序问题

| 来源 | 归属 | 证据 |
|---|---|---|
| arena 只增不减、按 2 的幂扩张 | 库的默认值 | 空载 615 MiB → 跑一块 3.2 GB |
| arena 挂在 `InferenceSession` 上，不能跨 session 共享 | 库的架构 | 四专家 = 四份 arena = 7.8 GB |
| memory pattern 为不重叠的中间张量各留一块 | 库的默认值 | 3.2 → 5.7 GB 的台阶，关掉即消失 |
| cuDNN workspace 取最大候选 | **已排除** | `cudnn_conv_use_max_workspace=0` 无任何变化（5739 MiB / 0.704 s） |
| `SessionPool` 的 key 只有 provider 名字、没有选项 | **程序** | 项目当时没有任何传参通道，只能吃默认值 |
| 四专家常驻、启动预热多建一个 session | **程序**（已修） | `MAX_RESIDENT_SESSIONS` 与 `prewarm` 已删 |

结论：**主要是库的默认策略面向长驻服务，程序的问题在于没有绕开池子去改它。** 不是库有 bug，但别指望默认值在 8 GB 笔记本上能用。

### 报错文案

OOM 不再把 ORT 的 `expr=cudaMemcpy(...)` 原样抛给用户。`app/errors.py` 的 `friendly_error()` 识别 `out of memory` / `CUDA failure 2` / `cudaErrorMemoryAllocation` / `OrtOutOfMemoryError`，翻译成三步可操作提示（关掉占显存的程序 → 换仅人声档 → `PITCHMORPH_PROVIDERS=CPUExecutionProvider` 强制 CPU），并保留原始错误首行供排查。

## 别再试（已量过且为负 / 已排除）

- **CPU 多专家并发**：85 s → 143 s。单 session 已吃满物理核，两个默认线程池 = 48 线程抢 24 个硬件线程。
- **GPU 多专家并发**：单块 0.70 → 1.38 s，显存 3.3 → 6.3 GB；`parallel=3/4` 按每专家约 3.2 GB 推算在 8 GB 卡上必然 OOM。
- **四专家常驻 / 启动预热**：OOM，已删。
- **只调 `arena_extend_strategy`**（含退回库默认的 `kNextPowerOfTwo`）：它只改空载那点零头（704 → 615 MiB）。3.2 → 5.7 GB 的台阶来自 memory pattern，关掉它才是关键。
- **`cudnn_conv_use_max_workspace=0`**：显存与耗时完全不变。
- **给单个 session 调 `intra_op_num_threads`**：见线程扫描，默认最优。
- **`onnxruntime-directml`**（零 CUDA 依赖的备选）：最新只到 1.24.4，落后 7 个版本，不建议。

## 待办

- [ ] **电源方案改「高性能」+ 接电源**，重跑一次 e2e，消掉 1.21 → 1.72 s 那 42% 的退化。零成本，唯一没做的免费项。
- [ ] **overlap 0.25 → 0.125**：`separator._chunked_infer` 的 `overlap = N_SAMPLES // 4` 与 `_transition_window` 的 `overlap_fraction=0.25` 两处必须同改。块数 69 → 59（−14.5%）。**会改变输出音频**，块边界平滑量减少，须按 AGENTS.md 的逐采样比对重新验收。
- [ ] **给每个 session 限 `intra_op_num_threads`（例如各 8）后再试 parallel=2**：这是并发能否翻案的唯一未验证路径。当时受限于 `SessionPool` 的 key 不含选项，现在 `runtime._build_session()` 已有传参通道，改造成本比当时低。
- [ ] **整首 quality（四专家 × 69 块）未跑**，只用 60 s 素材验证了四专家依次加载与释放。
- [ ] **全曲 `parallel=2` 与 `parallel=4` 未补测**；8 GB 卡上大概率 OOM，要测请用短素材。
- [ ] **`HEURISTIC` 换了卷积算法，与上游的逐采样比对还没做**：理论上 1e-6 级差异，验收线 < 1e-3。
- [ ] **显存余量守卫**：跑之前用 `nvidia-smi` 查空闲显存，按「约 3.3 GB × 批大小」判断，不够就拒绝或把并发降回 1，并把 `free_vram_mb` / `parallel_source` 暴露到 `/api/health`，让「为什么被降/为什么 OOM」能自解释。现在只在事后翻译报错，事前不检查。

## 复现

```powershell
uv run python scripts/bench.py samples/物凄.m4s                       # 短样本（12 s），日常迭代
uv run python scripts/bench.py samples/物凄.m4s --tier quality        # 四专家，依次加载/释放
uv run python scripts/bench.py samples/1333699443-1-30280.m4s --tier vocals   # 整曲（398.8 s）+ 显存峰值
$env:PITCHMORPH_PROVIDERS="CPUExecutionProvider"                      # 用同一条命令取 CPU 基线
$env:PITCHMORPH_CUDA_OPTS="arena_extend_strategy=kNextPowerOfTwo"     # 对照旧行为
uv run python scripts/setup.py                                        # 系统层 CUDA 诊断（只读）
```

`bench.py` 的分工：`runtime.ensure_session` 计加载、`separator._write_stem` 计写盘、`session.run` 计每块耗时，`nvidia-smi` 在后台线程按 0.2 s 采显存峰值。10-27 那次诊断用的临时插桩脚本（`%TEMP%` 下，已不存在）就是这几件事的草稿。
