# 性能诊断：分轨为什么慢 / GPU 值不值

> 这是一份**按时间追加的实测档案**：2026-10-27 做诊断，10-28 落地并行与 CUDA。
> 只想看现在的结论，读下面「当前结论」；想追推导过程与原始数据，按日期往下读。

## 当前结论（2026-10-28）

1. 单次分轨 **95.4%** 的时间在逐块推理上，解码 / 加载 / 写盘合计 < 5%——想快只能动推理。
2. **默认档已改为「仅人声」**（`config.DEFAULT_TIER = "vocals"`）：只跑一个专家，工作量是四轨档的四分之一。
3. **并行默认 1**。16 核机器上 `parallel=2` 反而慢 68%（60 s 片段 85.4 s → 143.4 s），WorkingSet 从约 3 GB 涨到 9.7 GB；全曲跑到 10.2 GB 被系统终止。并行只对核数远多于 24 的机器有意义，用 `PITCHMORPH_PARALLEL` 调。
4. **CUDA 已落地**：`onnxruntime-gpu` 是常规依赖，启动时用 92 字节的 Identity 图真探针一次。收益门槛是「单块 < 0.57 s」，本机实测见 §GPU 实测。
5. 数值安全：`parallel=1`、`parallel=2`、以及改造前的串行实现，四轨输出逐采样 diff 均为 0。

> 以下的「诊断完成，优化未落地」是当时的原始状态，保留是为了对照。

日期：2026-10-27　当时状态：诊断完成，优化未落地

---

## 测量环境

| 项 | 值 |
|---|---|
| CPU | Intel i7-14650HX，16 核 / 24 线程 |
| GPU | RTX 5060 Laptop，8 GB，compute_cap **12.0（sm_120 / Blackwell）** |
| 驱动 | 617.42，CUDA UMD Version **13.4** |
| 电源方案 | **平衡**（`powercfg /getactivescheme`，可能压制持续性能） |
| ONNX Runtime | **1.31.1 CPU wheel**，`get_available_providers()` = `['AzureExecutionProvider', 'CPUExecutionProvider']` |
| 项目模型 | `htdemucs_ft` 四专家，fp16weights，单个 157.9 MB，四份权重已缓存 |
| 测试音频 | `samples/美丽之物.mp3`，398.9 s |

## 实测数据

### 端到端（quality 档，四专家，插桩计时）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 解码 mp3 → WAV | 0.60 s | 0.1% |
| 模型加载 ×4 | 19.78 s | 3.9% |
| **逐块推理（276 次 `session.run`）** | **488.6 s** | **95.4%** |
| 写盘 ×4 | 1.22 s | 0.2% |
| **合计** | **512.1 s（8 分 32 秒）** | overall RTF **1.28** |

单块中位数 **1.722 s**（块长 7.8 s，单专家 RTF ≈ 0.22）。
四次 session 加载分别为 3.99 / 7.58 / 4.15 / 4.06 s。

### 单块微基准（系统空闲、短时）

| 指标 | 值 |
|---|---|
| 首次 run | 1.42 s |
| 稳态 5 次中位数 | **1.21 s** |
| RTF（单专家） | 0.16 |

**同一块音频，短基准 1.21 s，长跑稳态 1.72 s，差 42%。** 短基准测到的是 boost 峰值，规划时必须用长跑数字。

### 线程数扫描（`intra_op_num_threads`，同一块输入）

| 设置 | 中位数 | RTF |
|---|---|---|
| 默认（0） | **1.19 s** | 0.15 |
| 24 | 1.80 s | 0.23 |
| 16 | 1.35 s | 0.17 |
| 8 | 1.87 s | 0.24 |
| 4 | 2.00 s | 0.26 |

结论：**默认已最优，线程调优无收益**。超订（24 线程）反而慢 51%。

## 根因（按贡献排序）

1. **四个专家各跑一遍整曲，占 95.4%。**
   `app/separator.py:394-419` 按 stem 循环，每个专家都完整跑完整首歌，只取输出矩阵对应行。
   399 s 音频 → 69 块 × 4 专家 = 276 次整图推理。
   图输出固定为 `(1, 4, 2, N)`，**无法只算一行**（`app/separator.py:311,316`；上游 `inference.py:306-307` 明确 "no per-stem-skip"）。

2. **持续满载后单块从 1.21 s 退化到 1.72 s（+42%）。**
   电源方案「平衡」+ 笔记本功耗墙。实际耗时曲线在 1.6–2.0 s 之间震荡，不是稳定值。

3. **每个专家重新加载模型，19.8 s（3.9%）。**
   `app/separator.py:415` 每 stem 结束 `session_pool().clear()`，下个专家重新读 158 MB 图并重跑 ORT 图优化。

4. **解码与写盘可忽略**（1.82 s，0.3%）。

5. **线程/并发无可调空间**：见上方扫描；`app/jobs.py:90` 的单 worker 不影响单任务耗时（推理期间不持锁）。

## GPU 可行性（2026-10-27 静态核查；结论已被后面的实测取代）

已核实的前提：

- `onnxruntime-gpu 1.31.1` 存在，CUDA extra 要求 **CUDA 13.0 + cuDNN 9（cu13）**，驱动 CUDA UMD 13.4 满足。
  依赖：`nvidia-cuda-runtime~=13.0`、`nvidia-cudnn-cu13~=9.0`、`nvidia-cufft~=12.0`、`nvidia-curand~=10.0`、`nvidia-cuda-nvrtc~=13.0`。
  体积约 1.2–1.4 GB（ORT-GPU 160 MB + cuDNN 416 MB + 其余）。
- 模型图算子扫描（字节级）：Conv 496 / MatMul 512 / LayerNorm 104 / Softmax 40 / Cast 3372，
  **无 STFT / DFT / Einsum 等易回落算子** → 跨设备回落风险低。

两个必须注意的坑：

1. **包冲突**：`demucs-onnx` 硬依赖 `onnxruntime`（CPU 包），而 ORT 官方明确 `onnxruntime` 与 `onnxruntime-gpu` 不能共存（同模块名、DLL 冲突）。
   落地需要独立 venv，或 `[project.optional-dependencies] gpu` + 排除 CPU 包；直接 `uv pip install` 会被下一次 `uv sync` 还原。
2. **收益未证实**：图是导出时锁死的 `batch=1`、`7.8 s` 定长输入，模型仅 79 M 参数，单块计算量小、算子多，GPU 上可能被 kernel launch 开销吃掉大半。估计 3–6 倍，**必须实测**。

若真上 GPU，**必须同时去掉每 stem 的 `session_pool().clear()`**：CUDA 图编译远慢于 CPU，4 次重载会吃掉收益；8 GB 显存放 4 个常驻 session（约 2.4 GB）足够。

`onnxruntime-directml`（零 CUDA 依赖的备选）最新仅 1.24.4，落后 7 个版本，不建议。

## 待办（2026-10-27 视角；已完成的打勾见「落地与实测」）

- [x] **1. 默认档位换档**（`app/config.py:93`，改 1 行）
      **实际选择的是 `vocals`（仅人声）而不是 `fast`**：只跑一个专家，算力约为四专家档的四分之一。
      配套改了 `/api/models` 的返回顺序（前端取第一项作为默认选中），否则只改常量界面不会跟。
- [ ] **2. 电源方案改「高性能」+ 接电源**，消掉 1.21 → 1.72 s 那 42% 的退化。零成本，重新跑一次 e2e 即可验证。
- [x] **3. 去掉每 stem 的 `clear()`**（`app/separator.py:389,415`）
      以「批后 clear」实现：批内主线程预建 session → 并发跑 → join → `clear()` → 下一批。
      **注意这里并没有"第二单省 16–20 s"的收益**——每个专家都要加载一次，批后清池意味着下一个任务照样重新加载；真正跨任务复用需要完全不 clear（常驻 2.4 GB），本轮没做。
      不改变数值结果：批式 parallel=1 与改造前的串行实现逐采样比对 **max abs diff = 0**。
- [ ] **4. overlap 0.25 → 0.125**（`app/separator.py:293` + `:296` 两处必须同改）
      块数 69 → 59（−14.5%）。**会改变输出音频**，块边界平滑量减少，需按 AGENTS.md 的逐采样比对重新验收。
- [ ] **5. 上 CUDA**（独立 venv）——收益与第 1 条重叠，建议等 1、2 落地后再评估。

按 3 分钟歌曲折算：现在约 3.9 分钟；做完第 1、2 条后约 1 分钟。

## 当时的计划（2026-10-28，均已执行）

### 1. CPU 多专家并行推理（先做）

**目标**：quality 档下同时跑 2 个专家，压缩 512 s 这个数字。

**必须先认清的前提**：单个 session **已经吃满 16 个物理核**（线程扫描：默认 1.19 s 最优，24 线程反而 1.80 s）。
所以"多线程调用模型"不是给单个 session 加线程（加了更慢），而是**同时驱动两个独立的 session**。
预期收益 **0 ~ 34%，中性约 17%（约 −58 s）**：两个 session 各用默认线程池 = 48 线程跑 24 个硬件线程，超订会吃掉线性收益。

**必须一起改的三处（漏一处就会出 bug）：**

| 位置 | 问题 | 改法 |
|---|---|---|
| `app/separator.py:415` | 每 stem 的 `session_pool().clear()` 会清空**整个池**；并行时线程 B 会把 A 正在用的 session 清掉，下次 get 重建（+4 s） | 移出循环，或并行分支里彻底不调 |
| `app/separator.py:402-403` | 回调 `report((base + done/chunks)/total)` 在并行下会**让进度倒退**，前端百分比来回跳 | 改成"全局已完成块数 / 总块数"，或加锁取单调最大值 |
| `app/jobs.py:90` | `max_workers=1` 是刻意限制，不要动 | 并行只放在 `separate_stems` 内部，跨任务仍串行 |

**内存**：并行 2 个 session 约 1.2 GB（4 个常驻约 2.4 GB）。

**验收标准**：
- 用 `samples/美丽之物.mp3` 跑 quality，与本文 **512.1 s** 对比
- 四轨输出与串行版本逐采样比对，max abs diff 应 < 1e-3（`_chunked_infer` 无跨调用状态，并行不应改变数值）
- 若总耗时反而上升（超订），把并行度退回 1，不要硬留

### 2. 本机 CUDA 实测（后做）

**环境隔离**（不要污染主 `.venv`）：

```powershell
uv venv .venv-gpu --python 3.11
uv pip install --python .venv-gpu\Scripts\python.exe "demucs-onnx==0.3.4" "onnxruntime-gpu[cuda,cudnn]"
uv pip uninstall --python .venv-gpu\Scripts\python.exe onnxruntime
```

最后一行是关键：`demucs-onnx` 会拉 CPU 版 `onnxruntime`，与 GPU 版同模块名冲突，必须卸掉。约 1.2–1.4 GB 下载。

**验证 EP 真的生效**：
- `ort.get_available_providers()` 应含 `CUDAExecutionProvider`
- `session.get_providers()[0]` 应是 `CUDAExecutionProvider`
- 若是 CPU，用 `sess_options.log_severity_level = 0` 看节点分配到哪个 EP（排查算子回落）

**对比方法**：同一个 7.8 s 块、同一段 `美丽之物.mp3`，与 CPU 的 **1.72 s/块** 对比。
记录：单块耗时、**首次加载**（CUDA 图编译可能 10–30 s）、显存占用、总耗时。
GPU 上**务必让 4 个 session 常驻**，不要每 stem `clear()`，否则图编译开销吃掉收益。

**判定门槛**：

| GPU 单块耗时 | 判定 |
|---|---|
| < 0.57 s（3×） | 值得落地，改 pyproject 加 `gpu` extra |
| 0.57–1.15 s（1.5–3×） | 先看第 1 条并行能到多少，再决定是否叠加 |
| > 1.15 s（<1.5×） | 放弃 GPU，留在 CPU |

## GPU 实测（2026-10-28，已落地）

环境：RTX 5060 Laptop 8 GB / 驱动 617.42（CUDA UMD 13.4）/ 系统 CUDA Toolkit 13.4（DLL 在 `bin\x64`）/ `onnxruntime-gpu 1.31.1` + `nvidia-cudnn-cu13 9.27`。

**同条件对比**（60 秒素材，vocals 档，11 个 chunk）：

| | CPU | GPU | 倍数 |
|---|---|---|---|
| 单块 | 1.227 s | **0.702 s** | 1.75× |
| 推理总耗时 | 13.9 s | 8.4 s | 1.65× |
| 全流程 | 17.9 s | 12.6 s | 1.42× |

**首次运行有 CUDA 图编译**：8 秒素材（2 个 chunk）首测 3.78 s/chunk，第二次才回到稳态。短样本测不出真实收益，至少要跑到 10 个 chunk。

**结论：加速 1.75×，未达 3× 门槛。** 但对比 CPU 并行（实测 85 s → 143 s，反向优化），GPU 是唯一稳定有效的方向。

未达 3× 的可能原因（未逐项验证）：`batch=1` + 定长 7.8 s 输入让 kernel launch 与 H2D/D2H 占比过高；模型仅 79 M 参数，单块算力喂不满 GPU；电源方案仍是「平衡」，CPU/GPU 都降频。

**踩到的两个坑（已修）**：

1. **探针假阳性**：最小 Identity 图不经过卷积，cuDNN 缺失时照样跑通并报「CUDA 可用」，真实模型在第一个 Conv 崩掉。已加 `runtime._missing_cudnn()` 单独检查 `cudnn64_9.dll`。
2. **`uv sync` 会删除依赖图外的包**：手工 `uv pip install nvidia-cudnn-cu13` 装的东西在下一次 sync 被清掉。已把 cuDNN 写进 `[project.dependencies]`（Windows 标记），并把 `demucs-onnx` 的 `Requires-Dist: onnxruntime` 用 `override-dependencies` 改写成仅 macOS 生效——这样 Windows/Linux 上只装 GPU 包，`uv sync` / `uv run` 都不再需要额外参数。

## 复现方法

临时脚本放在系统临时目录（不在仓库内，关机可能被清理）：`%TEMP%\pitchmorph-bench\`

```
bench_chunk.py   单块基准 + 线程扫描 → out/chunk.json
bench_e2e.py     端到端插桩     → out/e2e.json（含 276 条 run 序列）
```

重建要点（直接用项目 venv，**不要 `uv run`**，避免触发依赖解析）：

- 取模型路径：`separator._resolve_paths("htdemucs_ft_vocals", ["vocals"])["vocals"]`
- 块长常量：`demucs_onnx.inference.N_SAMPLES` = 343,980（7.8 s @ 44.1 kHz）
- 端到端插桩点：包装 `SessionPool.get`（计加载）、`separator._write_stem`（计写盘）、`separator.decode_to_wav`（计解码），
  并把 session 包一层记录每次 `run` 的耗时；推理耗时 = 总耗时 − 其余三项。
- 环境：`HF_HUB_OFFLINE=1`，不设 `PITCHMORPH_PROVIDERS`（保持 CPU）。

## 已知陷阱

- **不要用短基准做规划**：1.21 s 是 boost 峰值，长跑稳定值是 1.72 s。
- `demucs_onnx.inference._make_session` 的 pool key 是 `(path, "", tuple(providers))`，**不含 SessionOptions**；
  monkeypatch 后必须 `session_pool().clear()` 才会重建。
- 上游 `cli.py` / 文档里 "htdemucs ~30% faster than FT bag" 的说法在 ONNX 场景不成立：这里是 4 张独立图逐张跑，实际接近 4×。

## 2026-10-28 落地与实测

### 已落地

- `separate_stems` 支持批式并发（`parallel` 参数 + `PITCHMORPH_PARALLEL`），前端提交任务时可选 1–4，单轨档位置灰。
- 默认档位 `quality` → `vocals`（仅人声）；`/api/models` 按默认档优先排序，否则前端仍会选第一项。
- 进度改为「各 stem 完成比例之和 / stem 数」，锁内取单调最大值再上报；取消用批内共享 `threading.Event`，任一 worker 失败其余在下一个块边界退出。

### 实测（同日，电源方案仍为「平衡」）

| 场景 | parallel=1 | parallel=2 | 结论 |
|---|---|---|---|
| 60 s 片段（quality 档） | **85.4 s** | **143.4 s** | 并行慢 68% |
| 398.9 s 全曲（quality 档） | **520.0 s** | 未跑完：批 2 中途进程被系统终止，WorkingSet 10.2 GB | — |

- 短样本并行时 WorkingSet 峰值约 **9.7 GB**。"批大小 × 0.6 GB"的估算完全不成立——ORT 的 CPU arena 随线程数膨胀。
- 数值一致性：批式 parallel=1 与改造前的串行实现（`%TEMP%\pitchmorph-bench\out\e2e\`，10-27 产出）四轨 **max abs diff = 0**；parallel=1 与 parallel=2 之间四轨也 **= 0**。并发不改变数值。
- 全曲 parallel=1 比 10-27 的 512.1 s 慢 1.5%，属于温度/电源状态漂移。

### 结论

**默认并行度定为 1**，并行能力作为可选开关保留（`PITCHMORPH_PARALLEL` / 前端控件），不做删除。

根因与线程扫描一致：两个默认线程池的 session 等于 48 线程抢 24 个硬件线程，超订代价大于重叠收益。真要并行，得先给每个 session 限制 `intra_op_num_threads`（例如各 8 线程）——这需要绕开 `SessionPool` 的建 session 路径（池 key 不含 `SessionOptions`），改造成本与收益都还没验证。

### 待验证（用短素材）

- 全曲 `parallel=2` 的实际耗时（本轮没跑完）；短样本的 143.4 s 里还含两次 session 加载（约 14 s）。
- `parallel=4` 未测。

---

## 2026-10-28 · CUDA 显存与「不常驻」

一次 `quality` 档任务在 RTX 5060 Laptop（**8151 MiB**）上崩了：

```
CUDA failure 2: out of memory
expr=cudaMemcpy(dst_data, src_data, bytes, cudaMemcpyHostToDevice)
```

失败点是**把 2.7 MB 的输入拷进显存**——设备已经满了，不是这次推理吃得多。随后一串 `cudnnDestroy ... CUDNN_STATUS_INTERNAL_ERROR` 是坏掉的 context 在退出时的次生现象，不是独立问题。

### 实测

逐个加载 `htdemucs_ft` 的四个专家，各跑一个真实分块：

| 状态 | 显存 |
|---|---|
| 空载 | 96 MiB |
| + drums | 3308 MiB |
| + bass | 6534 MiB |
| + other | 7814 MiB |
| + vocals | 7836 MiB |

**一个专家跑完一个分块后要占 2.2–3.2 GB**，而刚建好时只有 ~700 MiB——CUDA arena 按 `kNextPowerOfTwo` 增长，跑完不回缩。

旧策略 `MAX_RESIDENT_SESSIONS = 4`（"the whole bag stays resident"）是 CPU 时代的假设：CPU 上常驻只花内存、省 4 s 加载。到 GPU 上四个专家 = 7.8 GB，加桌面占用必然 OOM。

### 决策：会话不常驻

每批结束即 `sessions.clear()` + `session_pool().clear()`。实测重建成本：

```
round 0: build 3.9s  first chunk 1.2s
round 1: build 4.0s  first chunk 0.7s
round 2: build 4.1s  first chunk 0.7s
```

约 **4 s/专家**（CUDA 图编译为主，与磁盘缓存无关），`quality` 档四个专家合计 16 s——远小于一次失败重来的代价。

释放顺序有讲究：`clear()` 只断开池里的引用，局部字典仍吊着 session 时显存不会还，所以必须先 `sessions.clear()`。

### 仍未解决的问题

即使每批只留一个 session，**单个 session 在跑第 2 个分块时 arena 仍会从 3.4 GB 涨到 ~7.5 GB**（12 s 素材，2 个分块）。也就是说 8 GB 卡上现在只是"刚好跑完"，浏览器再占 1 GB 就会重新 OOM。

根因是 `kNextPowerOfTwo` 的分配策略。修法是给 CUDA session 传：

```python
("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})
```

空载 session 实测 704 → 516 MiB（-27%），峰值未测。**但 `demucs_onnx.SessionPool` 的 key 只含 provider 名字、不含选项**，要传就得自己建 session（我们本来就已经不用它的 `separate()`，会话缓存其实该归 `runtime` 管）。这一步尚未实施。

---

## 2026-10-28 · 会话归口与显存实测（已实施）

上一节的「仍未解决」已经修完。改动集中在 `app/runtime.py`：会话缓存从 `demucs_onnx.SessionPool` 收回 `runtime._SESSIONS`，第一次有了传 CUDA provider options 的地方。文中此前的 `session_pool().clear()` 现在一律是 `runtime.release_sessions()`。

### 生效的选项

| 选项 | 值 | 作用 |
|---|---|---|
| `arena_extend_strategy` | `kSameAsRequested` | arena 按实际申请量扩张，不再按 2 的幂预留 |
| `cudnn_conv_algo_search` | `HEURISTIC` | 跳过逐形状 benchmark，不保留试探过程中的最大 workspace |
| `enable_mem_pattern`（SessionOptions） | `False` | 关掉按形状的中间张量预分配，**降幅最大的一项** |

`PITCHMORPH_CUDA_OPTS="k=v,k=v"` 可覆盖或追加前两项，做 A/B 不必改代码。

### 逐步显存（vocals 专家，单 session）

| 阶段 | 修复前 | 修复后 |
|---|---|---|
| 空载 build | 704 MiB | 615 MiB |
| 跑完第 1 块 | 3245 MiB | 3245 MiB |
| 跑完第 2 块 | **5675 MiB** | **3245 MiB** |
| 跑完第 3 块 | 5675 MiB | 3245 MiB |
| `release_sessions()` 后 | 195 MiB | 195 MiB |

第 2 块那 2.4 GB 的台阶来自 memory pattern：形状固定时它为每个中间张量各留一块，生命周期不重叠的也不复用。关掉后峰值从第一块起就是稳态。

`cudnn_conv_use_max_workspace=0` 单独试过，**显存与耗时都完全不变**（5739 MiB / 0.704 s），可以直接排除 cuDNN workspace 这条假设。

### 端到端（`bench.py`，含显存采样）

| 场景 | 加载 | 推理 | 单块中位 | 显存峰值 | 全流程 |
|---|---|---|---|---|---|
| vocals / 12 s | 4.34 s | 2.7 s | 0.752 s | 3309 MiB | 7.3 s |
| vocals / 60 s | 3.97 s | 8.2 s | 0.704 s | 3309 MiB | 12.5 s |
| vocals / 398.9 s（整首，69 块） | 3.95 s | 49.5 s | 0.715 s | 3309 MiB | 54.7 s（RTF 0.14） |
| quality / 60 s（四专家） | 16.16 s | 31.9 s | 0.716 s | 3309 MiB | 48.8 s（RTF 0.81） |

quality 档峰值与单专家**相同**，证明每批释放真的把显存还回去了。修复前同一场景是 7836 MiB 且 OOM。

单块 0.704 s 与 `kNextPowerOfTwo` 时代的 0.702 s 一致，说明关闭 mem pattern 没有付出耗时代价。

同一段 60 s 素材的 CPU 基线（`PITCHMORPH_PROVIDERS=CPUExecutionProvider`）：

| | CPU | GPU | 倍数 |
|---|---|---|---|
| 单块中位 | 1.412 s | 0.704 s | **2.01×** |
| 推理（11 块） | 15.7 s | 8.2 s | 1.91× |
| 全流程 | 22.9 s | 12.5 s | 1.83× |

比 `kNextPowerOfTwo` 时期的 1.75× 略好，两次的差异在温度/电源漂移范围内——关键是显存优化没有牺牲速度。

### 归因：库特性 vs 程序问题

| 来源 | 归属 | 证据 |
|---|---|---|
| arena 只增不减、按 2 的幂扩张 | 库的默认值 | 空载 615 MiB → 跑一块 3.2 GB |
| arena 挂在 `InferenceSession` 上，不能跨 session 共享 | 库的架构 | 四专家 = 四份 arena = 7.8 GB |
| memory pattern 为不重叠的中间张量各留一块 | 库的默认值 | 3.2 → 5.7 GB 的台阶，关掉即消失 |
| cuDNN workspace 取最大候选 | **已排除** | `use_max_workspace=0` 无任何变化 |
| `SessionPool` 的 key 只有 provider 名字、没有选项 | **程序** | 项目没有任何传参通道，只能吃默认值 |
| 四专家常驻、启动预热多建一个 session | **程序**（已修） | `MAX_RESIDENT_SESSIONS` 已删、`prewarm` 已删 |

结论：**主要是库的默认策略面向长驻服务，程序的问题在于没有绕开池子去改它。** 不是库有 bug，但也别指望默认值在 8 GB 笔记本上能用。

### 报错文案

OOM 不再把 ORT 的 `expr=cudaMemcpy(...)` 原样抛给用户。`app/errors.py` 的 `friendly_error()` 现在识别 `out of memory` / `CUDA failure 2` / `cudaErrorMemoryAllocation` / `OrtOutOfMemoryError`，翻译成三步可操作提示（关掉占显存的程序 → 换仅人声档 → `PITCHMORPH_PROVIDERS=CPUExecutionProvider` 强制 CPU），并保留原始错误首行供排查。

### 并发开关：允许调，但实测是负优化

CUDA 下不再把 `parallel` 夹回 1（`jobs.py` 那段 clamp 已删），前端加了「并发专家」控件并标注实验性。实测（`samples/物凄.m4s`，12 s，quality 档）：

| | parallel=1 | parallel=2 |
|---|---|---|
| 单块中位 | 0.716 s（60 s 素材，44 次 run） | **1.381 s**（12 s 素材，12 次 run） |
| 显存峰值 | 3309 MiB | **6330 MiB** |
| 单专家推理 3 块 | 约 2.1 s | 约 4.1 s |

两个专家同时跑并没有把单块切短——它们抢同一块 GPU，单块耗时正好翻倍，总吞吐不变，显存却翻倍。这与 CPU 上 `parallel=2` 85 s → 143 s 同向：单 session 已经吃满设备，并发只是把同一份算力切成两半。

所以这个开关的定位是「让用户自己验证」，不是提速手段。默认 1，UI 文案直接写实测数字，避免有人开了之后以为变快。

### 未验证

- 数值一致性：`HEURISTIC` 换了卷积算法，理论上应有 1e-6 级差异。本轮比对脚本被取消，**尚未实测**。按 `_chunked_infer` 的验收规矩，跑 `demucs_onnx.separate(model="htdemucs_ft_vocals", precision="fp16weights")` 与本地路径逐采样比对，max abs diff 应 < 1e-3。
- 整首 `quality`（四专家 × 69 块）未跑，只用 60 s 素材验证了四专家依次加载与释放。
- `parallel=3` / `4` 未实测，按每专家约 3.2 GB 推算，8 GB 卡上必然 OOM；UI 的提示就是按这个判断写的。
- 电源方案仍是「平衡」，以上耗时未在「高性能」下重测。

### 复现

`scripts/_cold_probe.py` 的逐步探测（build / 首块 / 重建成本）已并入 `bench.py` 的显存采样，那个脚本留作应急，不再是必跑项。

```powershell
uv run python scripts/bench.py samples/美丽之物.mp3 --tier vocals    # 单专家整首 + 显存峰值
uv run python scripts/bench.py samples/美丽之物.mp3 --tier quality   # 四专家依次
$env:PITCHMORPH_CUDA_OPTS="arena_extend_strategy=kNextPowerOfTwo"     # 对照旧行为
```
