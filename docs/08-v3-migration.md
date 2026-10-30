# 08 · V2 → V3 迁移清单（2026-10-30）

V3 目录已建在 `C:\Users\Acer\daily\coding\Python\PitchMorph-V3`（当时不是 git 仓库）。
本文按「迁移价值」分级，避免把 V2 的历史包袱一起搬过去。

---

## A. 直接搬（资产，不用改）

| 路径 | 体积 | 用途 |
|---|---|---|
| `temp-dev/spike/models/fcpe_net.onnx` | 41.3 MB | **FCPE 网络，已验与 PyTorch 数值等价（CPU RPA 100%）**，V3 直接加载 |
| `temp-dev/spike/models/GAME-1.0.3-medium-onnx.zip`（已解包同名目录） | 171.4 MB | GAME 1.0.3 medium：encoder 38.9 / estimator 71.0 / segmenter 77.0 MB + bd2dur/dur2bd + config.json；DML 上已验证数值等价 |
| `temp-dev/spike/models/*.npz` | 2.3 MB | 数值回归的参考值（mel / latent / ref_f0），换引擎时可复用 |
| `temp-dev/samples/vocadito` | 72.4 MB | **40 首人工标注**（音符 + 帧级 F0 + 双标注者），V3 唯一的客观基准 |
| `temp-dev/samples/openutau_official` | 34.4 MB | 两首 OpenUtau 官方转写（含 188 s 那首），长音频与 USTX 对照 |

> 这些都不入 git（体积大、许可各异）。迁移方式是本地复制，V3 侧同样加进 `.gitignore`。

## B. 脚本与算法（逻辑照抄，语言可能重写）

| 路径 | 为什么值钱 |
|---|---|
| `temp-dev/spike/export_fcpe2.py` | FCPE → ONNX 的导出脚本（mel 放图外、opset 可参数化），换权重或换 opset 时可重跑 |
| `temp-dev/spike/probe_game.py` | **GAME 的完整 ONNX 推理胶水**：encoder → segmenter×8（D3PM 回填）→ bd2dur → estimator，含切片与参数；V3 的核心算法就是这个 |
| `temp-dev/spike/probe_dml.py` / `probe_latent.py` | DML 数值/速度的测量脚本，V3 换机器时用来复验 |
| `app/worker/notes.py` 的 `_CappedSlicer` | 切片长度上限 + **在能量最低处切**（固定时长切会劈开长音），GAME 的 O(T²) 必须靠它 |
| `app/worker/onsets.py` | 谱通量起音（融合已证无效，但作为「为什么无效」的留档） |
| `app/domain/curve.py` | `fix_octave_jumps` / `median_smooth` / cents 换算；V3 曲线处理要等价重写 |
| `app/domain/notes.py` | `durations_from_boundaries`（= 上游 bd2dur，已验证 300 例等价）、`spans_from_segments`、`notes_from_spans`（模型音高优先、缺字段退回中位数） |
| `app/domain/midi.py` / `ustx.py` | 导出契约：两种 MIDI、USTX 字段；**注意 `DENSITY_SEC` 要按 D17 从 50 ms 改到 25 ms** |
| `app/exports.py` 的 `_prepare` | 处理顺序为先修倍频跳变再平滑（`segment` 内传 `smooth_window=1`，避免二次滤波）——这个顺序踩过坑 |
| `app/config.py` | `TIERS` / `TIER_SOURCES` / `tier_model`（UVR 式档位的原型）、`GAME_SIZES`、`looks_dry`、队列与 TTL 限额 |

## C. 契约与架构（设计要保留，代码重写）

| 位置 | 内容 |
|---|---|
| `app/protocol.py` + `app/orchestrator.py` | 子进程 + stdout 逐行 JSON 事件、日志走 stderr、取消 = kill + watchdog。**这是「换模型不动业务层」的边界，V3 别丢** |
| `app/main.py` 的 `_derive_meta` / `_parse_params` | `meta.json` 参数快照 + 入队前校验（非法值不流到 worker） |
| `app/jobs.py` | `scope`（separate/analyze/both）+ `params` + `parent_id`，参数创建时定稿（D14） |
| `app/errors.py` | 错误 → 中文可操作文案的分层 |
| `app/worker/separate.py` 的专家筛选 | `htdemucs_ft` 的 `weights` 是对角阵 → 只跑目标音轨的专家；**「仅人声」档的省时关键**，V3 ONNX 化时按同思路只导 vocals 专家 |

## D. 测试与评测（V3 的唯一尺子）

| 路径 | 用途 |
|---|---|
| `scripts/compare_notes.py` | 三种参考（USTX / vocadito CSV / 外部 MIDI）+ 统一指标 |
| `scripts/eval_f0.py` | 帧级 RPA/RCA/八度错，用 vocadito 人工 F0 |
| `scripts/eval_vocadito.py` | 音符级 F1 + 人类天花板（A1 vs A2） |
| `tests/domain/*` | 纯函数行为规格（curve/notes/midi/ustx），换语言时可当验收用例逐条对照 |
| `tests/test_worker_notes.py` | `_CappedSlicer` 的三条约束（不超长、不丢样点、offset 连续） |

## E. 文档（直接复制，V3 侧当输入）

| 文件 | 价值 |
|---|---|
| `V3 构想.md` | 你的方向（已在 V2 仓库，V3 侧应作为根文档） |
| `docs/01-decisions.md` | D1–D19，尤其 **D14**（参数创建时定稿）、**D17**（不做平滑、密度 25 ms）、**D18**（V2 调参结论作废）、**D19**（FCPE 走 CPU） |
| `docs/05-game.md` | GAME 的体积/速度/质量实测、ONNX 可行性、气口定位——**别再从零试参数** |
| `docs/06-v3.md` | V3 技术方案、档位设计、DML 版本事实、技术栈候选 |
| `docs/07-spike.md` | 本次 spike 的完整数据（DML 对 GAME 等价、对 FCPE 失真） |
| `docs/00-replication.md` | V1 的坑表，其中 3 条跨路线成立：`/media` 挂载、`v-show` 白屏、`--reload` |
| `docs/04-ustx.md` | USTX 字段契约（对照 OpenUtau 源码核实过） |
| `.agent/memory/` | 跨会话记忆（含 V3 已确认的技术事实） |

## F. 明确不要搬

| 内容 | 原因 |
|---|---|
| `app/worker/game/_vendor/`（58 个 py） | PyTorch 版 GAME 推理，V3 用 ONNX，留着只会混淆 |
| `app/worker/separate.py` / `pitch.py` | torch 实现；只作算法参考（各算法要点已在 B/C 里摘出） |
| `pyproject.toml` 的 torch / lightning / matplotlib 等 | V3 运行时不装 PyTorch |
| `app/static/`、`frontend/node_modules`、旧 Vue 组件 | MD3 重写；旧前端只参考交互结论（`docs/02` 已写） |
| `data/jobs/` | 旧任务与旧格式，V3 不兼容，交给 TTL 或手动删 |
| `docs/perf-audit.md` | V1 数据，仅历史参考（关键结论已进 `docs/00`） |
| `docs/02-frontend.md` | V2 前端实现细节，MD3 重写时不适用；只保留「视图切换、波形解析」两条经验 |

---

## 迁移动作建议（顺序）

1. V3 仓库先建 `.gitignore`（`temp-dev/`、权重、`node_modules`），再复制 A 类资产
2. 复制 E 类文档，把 `docs/06-v3.md` 与 `docs/07-spike.md` 合并进 V3 的设计文档
3. 复制 B/D 类脚本与测试（先不跑，等 V3 骨架起来）
4. `app/domain` 的四个文件与 `tests/domain` 一起搬，作为「算法对照实现」
5. V2 仓库保持只读；`v2` 分支已 ff 到 `dev`（`5fb559f`），未推远端

## 迁移前要确认的两件事

- **许可**：vocadito 是 CC BY（需署名），OpenUtau 官方转写的来源需你确认；UVR 提纯模型尚未选型，许可未知
- **权重不入库**：GAME / demucs / FCPE 加起来约 500 MB，V3 同样走「首次下载 + 校验哈希」
