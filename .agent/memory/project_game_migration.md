---
name: 音符切分从启发式改走 GAME 模型
description: T0（谱通量起音 + 半音游程）对照 OpenUtau 官方转写后判定不够用，决定后续引入 GAME 架构；附对照数据与基准脚本位置
type: project
---

2026-10-29 决定：**音符切分（边界 + 音符音高）不再继续调启发式参数，后续改由 GAME 架构解决**（encoder + segmenter + bd2dur + estimator，上游 openvpi/GAME 是 PyTorch、MIT、`.pt` 权重）。

**Why:** 用 `temp-dev/samples/openutau_official/` 里两份 OpenUtau 官方转写当基准，跑 `scripts/compare_notes.py` 对照，启发式的上限明显不够：

| 样本 | 参考音符 | 本工具(默认) | 本工具(调参后最佳) | 起音召回 | 音高一致 |
|---|---|---|---|---|---|
| 物凄（12 s） | 48 | 37 | 41 | 45.8%→62.5% | 67.5%→84.8% |
| 科学MIMI（188 s） | 751 | 412 | 534 | 45.8%→57.5% | 67.5%→76.6% |

调参后仍有 15%–23% 的音高点错（多差 1 个半音）、40%+ 的边界漏检，且同音高相邻音符只有参考的一半。
继续调参是在 2 个样本上过拟合，不是通解。

**How to apply:**
- 再评估音符切分/任何新模型，一律用 `scripts/compare_notes.py`（`--pitch features/pitch.json` 或
  `--midi <模型导出的 mid>`，`--ref <ustx>`）。指标是 起音召回/精确 + 音高一致率。
- 参考 USTX 是 GAME 的输出，不含 pitd 曲线，只有音符；比对只比音符，不比曲线。
- **验收基准用 `科学MIMI`（188 s / 751 音符），不要用 `物凄`（12 s）。** 2026-10-30 M0 实测：
  GAME 1.0.0 在 科学MIMI 上是 750 音符 / 起音召回 98.4% / 音高一致 97.3%（small），
  medium 98.2%；而 物凄 只有 88%，且与模型体积无关——那份 USTX 大概率不是从 `stems/vocals.wav` 转写的。
- **体积取 small**（48.5 MB / 峰值 305 MiB / 188 s 用 10.9 s），medium 只多 0.9% 精度但多 148 MB。
- 上游 `numpy<2.0.0` 的钉子只影响训练/预处理：实测 numpy 2.4.6 下推理正常（97.8%），
  不必降主环境 numpy。`matplotlib` 是 `inference/callbacks.py` 的顶层 import，必须装。
- 现有的谱通量起音（`app/worker/onsets.py` + `features/onset.json`）保留：它产出的是帧号数组，
  GAME 的边界也可以走同一个出口，切分与前端不用重写。
