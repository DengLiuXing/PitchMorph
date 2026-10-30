# 05 · 接入 GAME 音符模型

> 状态：计划（`dev` 分支）
> 前置：`docs/03-pytorch-dual.md`、`docs/04-ustx.md`
> 结论来源：`scripts/compare_notes.py` 对 OpenUtau 官方转写的实测对照，见 §1

## 1. 为什么要接

启发式切分（半音游程 + 60 音分迟滞 + 120 ms 最短时长 + 谱通量起音）已经调到上限。用 OpenUtau 官方导出的 USTX 当基准：

| 样本 | 参考音符 | 密度 | 本工具默认 | 本工具调参最佳 | 起音召回 | 音高一致 |
|---|---|---|---|---|---|---|
| 物凄（12 s） | 48 | 4.18/s | 37 | 41 | 45.8% → 62.5% | 67.5% → 84.8% |
| 科学MIMI（188 s） | 751 | 4.08/s | 412 | 534 | 45.8% → 57.5% | 67.5% → 76.6% |

三个缺口都不是参数问题（补测：同一口径下 GAME 1.0.0 small 在 188 s 样本上是 起音召回 98.4%、音高一致 97.3%，见 §4 M0 实测）：

1. **密度差一半**：参考 4.1 音符/秒，我们 2.2–2.9。差的是"同音高换字"，音高游程里没有这个信息。
2. **边界召回 ≤62%**：谱通量只能抓辅音爆破；连音换字、气声过渡、弱起，那位置本来就**没有**能量突变可检测。
3. **音高一致 ≤85%**：16%–20% 的音符差 1 个半音，来自 FCPE 的 f0 误差叠加半音量化。`estimator` 与边界联合训练，这一点上不是同一量级。

基准脚本（复测用同一口径）：

```powershell
uv run python scripts/compare_notes.py `
  --pitch data/jobs/<id>/features/pitch.json `
  --ref temp-dev/samples/openutau_official/物凄芙兰.ustx

# 外部模型（GAME 等）的产物走 --midi，指标与上面完全同口径
uv run python scripts/compare_notes.py `
  --midi temp-dev/gamecheck/work/med/物凄_vocals.mid `
  --ref temp-dev/samples/openutau_official/物凄芙兰.ustx
```

## 2. 要接的是什么

上游 `openvpi/GAME`（MIT，PyTorch，权重 `.pt`）是四段流水线：

| 模型 | 输入 | 输出 | 在本项目里的定位 |
|---|---|---|---|
| `encoder` | 44.1 kHz 波形 | `x_seg`、`x_est`、`maskT` | 共享主干，必需 |
| `segmenter` | `x_seg` + language + 阈值 | 帧级 `boundaries`（D3PM 迭代） | **边界模型** |
| `bd2dur` | `boundaries` | `durations`、`maskN` | **不是模型**，是累积换算 → 我们自己写 |
| `estimator` | `x_est` + boundaries | 每段 `presence` + MIDI 值 | **音符模型** |

### 2.1 两条必须先接受的判断

**A. GAME 替代不了 FCPE。** `estimator` 每段只给一个整数 MIDI 值，音符**内部**的滑音、颤音、起音过渡它不输出。所以目标态是：

```
htdemucs → vocals.wav
FCPE     → features/pitch.json      连续 F0，供 pitd 曲线
GAME     → features/notes_game.json 边界 + 音符音高
```

两者帧栅格天然对齐：FCPE `hop=160 @16 kHz`、GAME `timestep=0.01`，都是 10 ms。

**B. `bd2dur` 用纯函数替代。** 它的输入输出只有 `boundaries/maskT → durations/maskN`，本质是"布尔切点累积成段长、段长之和 == 区间长度"。写在 `app/domain/notes.py` 里可测试、可复用，少一次模型加载。

## 3. 架构落点

不新增进程模型，不改变依赖方向：

```
worker/notes.py ──写──> features/notes_game.json ─┐
                                                  ├─> exports._prepare ─> domain.segment ─> MIDI/USTX
worker/pitch.py ──写──> features/pitch.json ──────┘
```

- **新阶段**：`python -m app.worker notes`，第 2 个 import torch 的地方
- **stage 注册**：`app/worker/__main__.py` 的 `_STAGES` 加一行
- **任务状态**：`app/jobs.Stage` 加 `NOTES` 并进 `ACTIVE`
- **编排**：`orchestrator` 加 `_notes_stage`，排在 `_pitch_stage` 之后
- **降级**：`features/notes_game.json` 不存在 → `segment()` 走现有启发式，行为与今天完全一致

### 3.1 文件名必须区分

`features/notes.json` 现在是 `write_exports` 的**输出**（派生结果）。GAME 的产物若也叫这个名字，就会出现"同一文件既是输入又是输出"。**新产物固定叫 `features/notes_game.json`**，`notes.json` 保持原语义不动。

## 4. 里程碑

每个里程碑都是可独立验收、可独立回退的单元。前一个不过，不做下一个。

### M0 · 环境与权重验证（不做任何代码改动）

在**隔离 venv** `temp-dev/gamecheck`（Python 3.12）里跑通上游，不碰主环境。

| 项 | 内容 |
|---|---|
| 动作 | clone 上游 → 装 `requirements.txt` → 下载 `.pt` → 对 12 s 素材跑 `infer.py extract` |
| 验收 | 产出 MIDI；用 `compare_notes.py` 与同一份 USTX 对照，**音高一致 ≥ 95%**、音符数 48±2 |
| 要回答 | 8 GB 卡能否推理；单次 12 s 耗时；D3PM 默认 nsteps 是否要降；推理是否需要 Lightning |
| 回退 | 删掉 `temp-dev/gamecheck`，主环境零影响 |

> 音高一致若明显低于 95%，说明参考 USTX 不是同一版 GAME 产出的（或模型版本不同），先查版本，不要往下走。

#### M0 实测结果（2026-10-30，隔离 venv `temp-dev/gamecheck`）

环境：Python 3.12.13 + `torch 2.11.0+cu128`（复用 uv 缓存，没有重下 2.5 GB）+ `lightning 2.6.6`。
依赖比上游 `requirements.txt` 少了 `onnx / onnxscript / onnxslim / tensorboard`，**但必须补 `matplotlib`**
（`inference/callbacks.py` 顶层 import 了它，装少了直接 ModuleNotFoundError）。
权重取 v1.0.0 release —— **只有它发 PyTorch `.pt`**，v1.0.1–1.0.3 全是 ONNX / `.oudep`。

| 项 | small（48.5 MB） | medium（190.5 MB） |
|---|---|---|
| 显存峰值 | 305 MiB | 453 MiB |
| 12 s 素材耗时（热启动，nsteps=8） | 9.9 s | 8.6 s |
| 188 s 素材耗时 | 10.9 s | 15.5 s |
| 本工具流水线内（2026-10-30 复测，188 s） | 12.4 s · 747 音符 · 音高 96.3% | 10.4 s · 760 音符 · 音高 **97.6%** |

验收（两个参考样本都跑同一口径）：

| 样本 | 权重 | 音符数（参考） | 起音召回 / 精确 | 音高一致 |
|---|---|---|---|---|
| 科学MIMI 188 s | small | 750（751） | 98.4% / 98.8% | **97.3%** |
| 科学MIMI 188 s | medium | 752（751） | 99.1% / 98.9% | **98.2%** |
| 科学MIMI 188 s | large | 764（751） | 98.5% / 96.9% | 95.8% |
| 物凄 12 s | small | 45–47（48） | 89.6% / 93.3% | 86.9–89.2% |
| 物凄 12 s | medium | 52（48） | 95.8% / 88.5% | 88.1% |

> **large 反而更差（95.8% / 764 音符）**，不是它弱，而是这条参考本身就是 medium 时代的 GAME 产出：
> 指标衡量的是「和参考一致」，不是「绝对正确」。large 的边界更细（多出 13 个音符），
> 碰到由 medium 定的参考就开始扣分。要判 large 的真实优劣，需要一份人工标注的基准，
> 现在没有。耗时 14.0 s、显存峰值 1061 MiB，都比 medium 差一截。

其余实测：

- **nsteps**：8→4 省 3 s，音符数与一致率都在噪声内；**降到 2 会崩**（80.3%，凭空多出 5 个音符）。默认 8 保留。
- **不确定**：同参数两次跑出 45 / 47 个音符（D3PM 从 `t0=0` 随机起采），任何对比指标都有 ±2 音符抖动。
- **`-l zh` 无收益**（43 音符、88.7%），语言条件不是这里的瓶颈。
- 冷启动 26.9 s 是首次 CUDA 编译，之后稳定；188 s 素材的 RTF ≈ 0.06（small）/ 0.08（medium）。

**结论：M0 过门（用 188 s 样本），默认 small，medium 作为可选精度档。**

- 长样本上 small 就有 97.3% 音高一致、98.4% 起音召回，音符数 750 vs 参考 751——**体积不是瓶颈**。
  接进本工具流水线后同一口径复测（2026-10-30）：small 96.3% / 747 音符、medium 97.6% / 760 音符，
  耗时 12.4 s vs 10.4 s（都被 ~6 s 启动时间主导，medium 并没有更慢）。
- 因此体积做成参数而不是档位：`notes.model = small | medium | large`，前端在创建弹窗里选「标准 / 高精度」。
  默认 small；需要更贴参考时用 medium。**large 不推荐**：上表的 95.8% 与 764 音符显示它在细切，
  且耗时 14.0 s、显存 1061 MiB 都更贵。它的权重已下（378 MB），留着做对照，不进前端选项。
- **12 s 的 `物凄` 样本不是合格基准**。它比长样本差 8–10 个百分点，且差距与体积无关；
  把比较收紧到参考音符中段 20%、或按起点配对逐个音符比，仍是 87–89%，说明不是边界精度问题。
  参考里 0.52 s / 0.94 s 两个 −31 dBFS 的轻声音符在任何体积下都不出现（贡献 6% 无覆盖），
  更像「这份 USTX 转写的不是 `stems/vocals.wav` 这条音轨」。**后续验收一律以 `科学MIMI` 为准**，
  `物凄` 只作辅助参考，且复跑同一命令时注意 D3PM 的 ±2 音符抖动。
- 顺带确认：参考 USTX 与 GAME 1.0.0 的 PyTorch 权重是**同一代**，不需要改走 ONNX 1.0.3 路线。

### M1 · 边界接入（音高仍用 FCPE）

只接 `encoder + segmenter`，这是最小可用切片。

| 项 | 内容 |
|---|---|
| 新增 | `app/worker/notes.py`（仅边界）、`app/domain` 里的 `boundaries → durations` 纯函数 |
| 改动 | `_STAGES`、`Stage`、`orchestrator`、`exports._prepare`、`schemas.PitchParams`（开关） |
| 音高 | 每段取 FCPE 段内中位数（现有 `_median_of` 逻辑） |
| 验收 | `compare_notes.py`：**起音召回 ≥ 90%**，音高一致不低于 M0 的 95% − 3 |
| 回退 | 删 `features/notes_game.json` 即回到启发式 |

#### M1 实测结果（2026-10-30）

「边界借用、音高仍用 FCPE」这条前提被数据推翻，所以 M1 与 M2 是**同一次接入交付**的。

| 边界来源 | 音高来源 | 音符数（参考 751） | 起音召回 / 精确 | 音高一致 |
|---|---|---|---|---|
| GAME | FCPE 段内中位数（M1 方案） | 753 | 98.3% / 98.1% | **83.2%** · 差 1 半音 15.2% |
| GAME | GAME estimator（M2 方案） | 747 | 97.6% / 98.3% | **96.5%** · 差 1 半音 2.4% |
| 启发式（同一条对照） | FCPE | 412 | 45.7% / 83.3% | 67.8% |

- 边界：GAME 边界接上后起音召回从 45.7% 直接到 98%+，M1 的 ≥90% 目标大幅超额完成。
- 音高：FCPE 段内中位数只有 83.2%，**达不到 M1 的「M0 − 3 = 92%」**，差 1 半音占 15.2%
  ——FCPE 在中位数上系统性地偏半个音是已知短板。改用它自己的 estimator 后立刻到 96.5%，
  所以 `notes_game.json` 直接带 `midi` 字段，`notes_from_spans` 收到 `hints` 就用模型音高，
  字段缺失才退回曲线中位数（降级路径有单测）。
- 747 vs 751 的差不是边界问题，是 D3PM 的随机采样（同参数多次跑在 745–753 之间）。

### M2 · 音高接入（接 estimator）

| 项 | 内容 |
|---|---|
| 新增 | `estimator` 推理；`presence` → 段是否有声 |
| 验收 | 音高一致 **≥ 95%**，且相对 M1 有提升；无提升就撤掉，保留 M1 |
| 回退 | 开关切回 M1 的"中位数定音高" |

> **已随 M1 一起交付**，结果见上表第二行（96.5%）。实现上不是「开关」而是数据形状：
> `notes_game.json` 的每段带 `midi` 就用它，没有就退回 FCPE 中位数。
> 落地位置：`app/worker/notes.py`（写）、`app/exports._game_notes`（读）、
> `app/domain/notes.py` 的 `spans_from_segments` / `notes_from_spans`。
>
> 与文档原计划的差异：原计划「M1 先只接 encoder + segmenter」，实际还必须接 estimator——
> 同一次 forward 里两个模型是一起跑的，省不掉，而且上面证明了省掉它会掉 13 个点。

### M3 · 工程化

| 项 | 内容 | 状态（2026-10-30） |
|---|---|---|
| 参数 | `nsteps` / `seg-threshold` / `seg-radius` / `est-threshold` / `language` 从前端暴露 | 后端 `NotesParams` + `POST /api/jobs/{id}/notes` 全通；前端只放了 `nsteps`（8/4/2），其余三个阈值等有实测依据再上 |
| 性能 | 长音频按静音切块（可复用 `AudioSlicer` 的参数：`threshold=0.02`、`hop=441`、最短 100 帧、噪声底 20 帧） | **不需要另做**：上游 `SlicedAudioFileIterableDataset` 内部就是按静音切片的 `Slicer`（`threshold=-40 dB`、`min_length=1000`、`min_interval=200`、`max_sil_kept=100`），188 s 素材切成 10 片、总耗时 10 s |
| 打包 | 权重不进 git；`pyproject` 只加推理必需的依赖；确认 vendoring 的源码范围 | 归位：`app/worker/game/_vendor/` 58 个文件逐字复制，`VENDOR.md` 锁 commit；权重走 `scripts/fetch_game.py`（默认 small，44.7 MB）并进 `.gitignore`；`pyproject` 只加了 8 个推理必需依赖 |
| 文档 | `AGENTS.md` 磁盘契约加 `notes_game.json`；`docs/04-ustx.md` 不变（导出层不动） | 已更新 `AGENTS.md`（磁盘契约、硬约束、架构）与 `.agent/memory/`；`docs/04-ustx.md` 未动 |

#### 已知差异：快速短音之间的气口（2026-10-30 定位，含 1.0.3 ONNX 复测）

`科学MIMI.ustx` 第 15–16 小节有三个互不相连的短音，我们的结果常把它们连成一片。逐音符对过数据，
问题不在切分，而在**「这段算有声还是休止」**：

| 时间 | 参考 USTX | 本工具（1.0.0 PyTorch） |
|---|---|---|
| 29.28–29.43 | midi 70 | midi 70 |
| 29.43–29.60 | **休止** | 常被并进相邻音符，或填成 midi 58 的假低音 |
| 29.60–29.76 | midi 70 | midi 70 |

一开始怀疑是版本（1.0.3 更新说明写着 *Improved V/UV flags*），于是把 1.0.3 的 ONNX 权重也跑了一遍
（可行性见下一节）。结论是**版本不是主因**：

| 配置 | 该窗口的行为 |
|---|---|
| 1.0.0 PyTorch，`est=0.2` | 合并（偶发 58 假低音） |
| 1.0.0 PyTorch，`est=0.5`，同参数跑三次 | **一次出现气口**（与参考一致）、一次合并、一次切开但无间隔 |
| 1.0.3 ONNX，`est=0.2 / 0.5 / 0.7` | 都合并 |
| 1.0.3 ONNX，`est=0.8` | 气口出现 |

所以这一处的本质是 **presence 概率正好压在阈值边缘 + D3PM 采样抖动**：PyTorch 路径每步会随机
去掉一部分边界，于是同一参数每次结果都不同；ONNX 路径是确定的，反而「稳定地合并」。
想稳定复现参考的休止，只能把 `est_threshold` 抬到 0.8 左右（会连带丢掉更多弱音），或者人工编辑。

**没有证据支持「换成 1.0.3 就更接近 OpenUtau」**：同口径下 ONNX 1.0.3 是 745 音符 / 音高一致 96.6%，
PyTorch 1.0.0 medium 是 753 / 97.8%。因此暂不切换。

#### GAME 1.0.3 的 ONNX 路线可行性（2026-10-30 验证）

结论：**技术上完全可行，且不需要 torch**，但这次不采用（上面那条质量结论）。

- **CUDA 复用**：`onnxruntime-gpu` 可以直接用 torch wheel 里的 CUDA 运行时（`cudart64_12` / `cublas64_12` /
  `cublasLt64_12` / `cudnn*_9` / `cufft64_11` / `curand64_10` / `nvrtc64_120`）。
  关键是**把 `torch/lib` 放进 `PATH`**，`os.add_dll_directory` 不够 —— provider DLL 的依赖链走默认搜索顺序，
  只注册目录会报 `depends on "cublasLt64_12.dll" which is missing`。ORT 1.24.1 与 1.31.1 都验证通过。
- **推理管线只要 ~150 行**：`encoder(waveform,duration)` → `segmenter` 循环 nsteps 次（`prev_boundaries` 回填）→
  `bd2dur` → `estimator`；mel 前端在图内，不需要 librosa/lightning/matplotlib。
- **必须按静音切片**：segmenter 的注意力是 O(T²)，188 s 整段要 5.6 GB 显存直接 OOM；
  用 `inference/slicer2.Slicer`（与 PyTorch 路径同一份参数）切成 10 段即可。
- **速度**：188 s 素材 3.6–5.1 s（PyTorch 路径 10.4 s）。代价是仓库里多一套运行时 + 一份手写胶水。

验证脚本留在 `temp-dev/onnxcheck/`（`game_onnx.py`、`check_cuda.py`），未入库。

M3 结束时仍未验证的两条：

- **12 分钟整曲没跑过**：目前最长验到 188 s。切片是逐片进模型的，显存不随长度涨，
  但 `librosa.load` 会把整首歌读进内存（12 min 单声道 44.1 kHz ≈ 127 MB），需要实测一次。
- **`quality` 档 + GAME 同时存在时未测**：现在是分轨子进程结束后才起音符子进程，
  显存不会叠加，但两个阶段的总耗时没在长素材上量过。

## 5. 依赖与许可

- **许可**：上游 MIT，可以 vendoring 源码与分发权重。权重与代码分别核对一次许可证文本。
- **vendoring 范围**：只搬推理路径（`inference/api.py` + `modules/` + 配置），不搬训练/预处理。锁定上游 commit。
- **新增依赖**：`onnxruntime` 不需要（走 PyTorch 原件）；可能要补 `librosa`（上游用它输出音名，仅文本产物需要，能去掉就去掉）。
- **Python**：已升到 3.12，与上游测试环境一致，不再是变量。

## 6. 风险

| 风险 | 判断 | 对策 |
|---|---|---|
| 8 GB 显存不够推理 | 未知，M0 回答 | 降 nsteps / 静音切块 / 分块串行 |
| D3PM 8 轮太慢 | 大概率 | 默认 2–4 轮，质量档再放 8 |
| 现有 `pitch` 阶段耗时占比被挤压 | 确定 | 重新分配进度 band（现在分轨 0.05–0.83、音高 0.84–0.97，没有第三段的余量） |
| 权重体积与下载 | 未知 | M0 记录实际大小，再决定随包分发还是引导用户下载 |
| tokenizer/语言条件用不上 | 低 | `language` 可选，默认 Auto |

## 7. 不变的东西

- `app/domain/` 仍不 import torch；主进程仍不 import torch
- 写盘仍先 `.part` 再 `os.replace`
- 无声帧仍写 `null`，不写 0
- 一次只驻留一个模型（`notes` 与 `pitch`、`separate` 是三个串行子进程）
- 完全划分（非静音区间不留洞）与音高取区间众数，这两条在任何模型下都成立
- `scripts/compare_notes.py` 的口径不变，M0–M2 都用它验收
