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

三个缺口都不是参数问题：

1. **密度差一半**：参考 4.1 音符/秒，我们 2.2–2.9。差的是"同音高换字"，音高游程里没有这个信息。
2. **边界召回 ≤62%**：谱通量只能抓辅音爆破；连音换字、气声过渡、弱起，那位置本来就**没有**能量突变可检测。
3. **音高一致 ≤85%**：16%–20% 的音符差 1 个半音，来自 FCPE 的 f0 误差叠加半音量化。`estimator` 与边界联合训练，这一点上不是同一量级。

基准脚本（复测用同一口径）：

```powershell
uv run python scripts/compare_notes.py `
  --pitch data/jobs/<id>/features/pitch.json `
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

### M1 · 边界接入（音高仍用 FCPE）

只接 `encoder + segmenter`，这是最小可用切片。

| 项 | 内容 |
|---|---|
| 新增 | `app/worker/notes.py`（仅边界）、`app/domain` 里的 `boundaries → durations` 纯函数 |
| 改动 | `_STAGES`、`Stage`、`orchestrator`、`exports._prepare`、`schemas.PitchParams`（开关） |
| 音高 | 每段取 FCPE 段内中位数（现有 `_median_of` 逻辑） |
| 验收 | `compare_notes.py`：**起音召回 ≥ 90%**，音高一致不低于 M0 的 95% − 3 |
| 回退 | 删 `features/notes_game.json` 即回到启发式 |

### M2 · 音高接入（接 estimator）

| 项 | 内容 |
|---|---|
| 新增 | `estimator` 推理；`presence` → 段是否有声 |
| 验收 | 音高一致 **≥ 95%**，且相对 M1 有提升；无提升就撤掉，保留 M1 |
| 回退 | 开关切回 M1 的"中位数定音高" |

### M3 · 工程化

| 项 | 内容 |
|---|---|
| 参数 | `nsteps` / `seg-threshold` / `seg-radius` / `est-threshold` / `language` 从前端暴露 |
| 性能 | 长音频按静音切块（可复用 `AudioSlicer` 的参数：`threshold=0.02`、`hop=441`、最短 100 帧、噪声底 20 帧） |
| 打包 | 权重不进 git；`pyproject` 只加推理必需的依赖；确认 vendoring 的源码范围 |
| 文档 | `AGENTS.md` 磁盘契约加 `notes_game.json`；`docs/04-ustx.md` 不变（导出层不动） |

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
