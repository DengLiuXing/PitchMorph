# AGENTS.md — PitchMorph v2

本地 Web 工具：一首混音进，四轨 WAV + 人声音高线 + MIDI + OpenUtau 工程出。
FastAPI 主进程**不加载 torch**；全部分轨与音高推理发生在 `python -m app.worker <stage>` 子进程里。

## 文档地图

| 文件 | 内容 |
|---|---|
| `docs/00-replication.md` | v1（ONNX）复刻指南，**历史参考**；其六条坑只有 `/media`、`v-show`、`--reload` 三条跨路线成立 |
| `docs/01-decisions.md` | 关键决策与各自的推翻条件 |
| `docs/02-frontend.md` | 前端交互与视觉基线（核心源码 `frontend/src/components/Workbench.vue`） |
| `docs/03-pytorch-dual.md` | 为什么从 ONNX 换到 PyTorch |
| `docs/04-ustx.md` | USTX 字段契约（对照 OpenUtau 源码核实） |
| `docs/05-game.md` | GAME 音符模型的接入、M0–M3 实测数据、ONNX 可行性（已接入，M3 完成） |
| `docs/06-v3.md` | V3 的约束（导出不做平滑）与重构清单；V2 已冻结时读它 |
| `docs/perf-audit.md` | **ONNX 时代的数据，v2 不适用**，只作历史参考 |
| `.agent/memory/` | 跨会话项目记忆，索引是 `MEMORY.md`（随仓库走，不要 ignore） |

## 构建与测试

普通构建与测试**直接跑**，跑完汇报结果。只有会下载几百 MB 或必须走代理的操作才停下来等用户（torch wheel、demucs / FCPE 权重）。删除文件仍要先说清路径与理由。

```powershell
uv sync                                  # 装依赖（torch cu128，约 2.5 GB，属于大文件）
uv run python scripts/probe_env.py       # 探针：sm_120 + FCPE 可用性
uv run pytest                            # 全部测试，约 1 s，不需要 GPU / 权重 / 起服务
uv run pytest tests/test_api.py -k cancel # 单个用例；可直接指定 tests/domain/test_notes.py
cd frontend; npm run typecheck; cd ..    # 只做 vue-tsc 类型检查，不写 app/static
cd frontend; npm run build; cd ..        # 前端构建，产物写 app/static（入库，勿手改）
cd frontend; npm run dev; cd ..          # Vite 开发服，/api 与 /media 已代理到 :8000
uv run uvicorn app.main:app --port 8000  # 启动（不要加 --reload）
```

冒烟与对照脚本（会碰 GPU 或需要先起服务，按需跑）：

```powershell
uv run python scripts/smoke_worker.py <素材> --tier vocals --pitch  # 直喂子进程，看协议事件流与产物
uv run python scripts/smoke_http.py <素材>   # 上传 → 轮询 → /media → Range 206
uv run python scripts/compare_notes.py --pitch data/jobs/<id>/features/pitch.json --ref <参考.ustx>
uv run python scripts/fetch_game.py          # 下载 GAME 权重到 app/worker/game/weights/（44.7 MB）
```

`compare_notes.py` 是音符切分的唯一验收口径，`docs/05-game.md` 的 M0–M2 都以它判定。

权重下载卡住：`$env:HF_ENDPOINT="https://hf-mirror.com"`、`$env:HF_HUB_DISABLE_XET="1"`。

## 架构

```
app/protocol     ← 父子进程的事件协议，纯函数，两边共用
app/config       ← 档位、路径、体积与时长上限
app/errors       ← OOM / 解码失败 / 无音轨 → 中文可操作文案
app/media        ← soundfile 优先，ffmpeg 兜底（-map 0:a:0 只取首条音轨）
app/jobs         ← 内存任务表 + 单 worker 队列 + TTL 清理
app/orchestrator ← Popen 起子进程，逐行解析 stdout 的 JSON
app/exports      ← pitch.json → notes.json / 两个 MIDI / USTX（毫秒级，主进程直接算）
app/packaging    ← 打包下载：stems 用 ZIP_STORED 落盘并复用，project 走内存
app/main         ← 路由 + /media 静态托管
app/domain       ← 纯函数：curve / notes / midi / ustx，不 import torch、不做 IO
app/worker       ← 只在子进程被 import，全项目唯一 import torch 的地方
app/worker/game  ← GAME 推理路径的 vendoring：`_vendor/` 是上游逐字副本（见 VENDOR.md）
```

依赖只能向下。`domain/` 与 `worker/` 互不 import，靠 `features/*.json` 解耦。

**任务按 `scope` 分三条路，参数在创建时定稿**（`run_pipeline` 里按 `job.scope` 分派）：

| scope | 流水线 | 产物 |
|---|---|---|
| `separate` | decode → separate | `stems/*.wav` |
| `analyze` | decode（或用父任务 vocals）→ pitch → notes | `features/*.json` + `exports/*` |
| `both` | decode → separate → pitch → notes | 两者 |

「改参数」= `POST /api/jobs/{id}/analyze` 用父任务的 `stems/vocals.wav` 新建一个 `analyze` 任务，
原任务与产物一律不动，两个任务并排比。没有「原地重跑」这种隐式覆盖。导出不是独立 stage，挂在音高阶段末尾。

**仅分析时必须是人声干声**：没有 `parent_id` 又选 `analyze` 时，文件名得带 `vocal/人声/干声/dry/acap`
之一，否则入口直接 400 —— 在混音上提音高只会得到被伴奏干扰的假音符（`config.looks_dry`）。

**导出是即时计算的，落盘文件不是读取源**：`GET /api/jobs/{id}/exports/{kind}?smooth=N` 直接调
`exports.render_export` 现算，`exports/` 里的文件只是默认平滑度那一份。要改导出行为就改 `render_export`，
直接改 `.mid` / `.ustx` 文件没有任何效果。

前端分层：`api/`（`client.ts` + `types.ts`，**出参形状改 `app/schemas.py` 时必须同步 `types.ts`**）、
`composables/usePlayer.ts`（四轨同步播放）、`lib/wavPeaks.ts`（直接解析 PCM-16 字节算峰值，不走
`decodeAudioData`）、`components/`（工作台主体是 `Workbench.vue`）。

## 子进程协议

stdin 一份 JSON 任务描述；stdout 逐行一个 JSON 事件；**日志一律走 stderr**。

```
{"t":"progress","frac":0.42,"detail":"segment 3/7"}
{"t":"done","artifacts":{"vocals":"stems/vocals.wav"}}
{"t":"error","message":"CUDA out of memory"}
```

两端都钉死在 UTF-8（`app/worker/__main__.py` 的 `_configure_streams`）：Windows 中文 locale 是 cp936，
父进程按 UTF-8 读，半个汉字就是坏行。父进程侧 `parse_line` 对坏行只返回 `None`、绝不抛异常。

取消 = `kill()`：`orchestrator` 另有 watchdog 线程，子进程安静下来（模型加载、大块推理）时也能立刻生效。
父进程不解析模型的任何内部状态，这是「换模型不动业务层」的边界。

## 磁盘契约

```
data/jobs/{id}/
├── source.*                  # 原始上传文件（扩展名保留），解码从这里读
├── input.wav                 # 44.1k 立体声 PCM16，retry 依赖它
├── meta.json                 # 源文件名 / scope / params / parent_id：restore() 与下载名都靠它
├── stems/{drums,bass,other,vocals}.wav
├── features/{pitch,onset,notes,notes_game}.json
├── exports/*.mid, *.ustx     # 默认平滑度的派生结果
└── package_stems.zip         # 打包缓存，源 wav 更新后自动重压
```

`features/` 与 `stems/` 平级：音高依赖 `stems/vocals.wav` 的稳定路径。
`features/notes.json` 是 `write_exports` 的**输出**，不是输入；GAME 的产物叫
`features/notes_game.json`（边界 + 模型音高），**删掉它导出就退回启发式**——这是设计的降级开关。
派生的分析任务没有自己的 `source.*` / `input.wav`，音频来自父任务目录（`parent_id` 写在 meta 里）。
取消/失败会删掉 `stems/` 与 `features/`，只留 `input.wav` 给重试。
限额都在 `app/config.py`：300 MB / 12 min / 队列上限 3 / 产物保留 7 天
（`docs/01-decisions.md` D10 里写的「24 h」已过期）。

## 硬约束

- **torch 只用 cu128 索引**。本机是 sm_120（Blackwell），cu126 的架构上限是 sm_90，装上去 GPU 直接不可用。
- **主进程不 import torch**。一旦引入，就失去了「OOM 只死子进程」这唯一收益。
- **写盘先 `.part` 再 `os.replace`**。否则取消后留半成品 wav，播放器会读到截断文件。
- **stdout 只走协议**，`print` 调试会污染父进程的解析。
- **一次只驻留一个模型**，跑完即退出；8 GB 卡放不下分离 + 音高两个模型。
- **`/media` 挂在 `data/jobs` 上**，URL 是 `/media/{id}/stems/vocals.wav`，不是 `/media/jobs/...`（v1 踩过）。
- **视图切换用 `.view.is-active` 类，禁用 `v-show`**（v1 白屏过）。
- **不要 `--reload`**：任务表在内存，改 `.py` 会丢进行中的任务。
- **无声帧写 `null` 不写 0**。0 Hz 在前端会被当成有效低音，画出贴着底边的假线。
- **`features/notes.json` 只作导出输出**，不要当输入读一遍再写回去（GAME 的产物是 `notes_game.json`）。
- **新增 worker stage 要守住 UTF-8 管道**：`_STAGES` 加一行，日志走 stderr，别往 stdout 塞协议之外的东西。
- **`app/worker/game/_vendor/` 不要手改**：那是上游逐字副本，升级时整目录替换（流程见 `VENDOR.md`）。
  它还会往 stdout 打配置和 tqdm，worker 里必须 `redirect_stdout(sys.stderr)`，否则协议通道被污染。
- **GAME 权重不入库**：`app/worker/game/weights/GAME-1.0-<体积>/model.pt`，路径单点在 `config.game_model_path(size)`；
  体积名（`small` / `medium` / `large`）是参数 `notes.model`，也可以是 `.pt` 绝对路径。
  找不到就跳过音符阶段、导出退回启发式，不许让整个任务失败。
- **当前 GAME 走 PyTorch 1.0.0，不走 ONNX**：1.0.3 的 ONNX 路线验证过可行（188 s 用 3.6–5.1 s，比 PyTorch 快 2×，
  且能直接复用 torch 的 CUDA 库 —— 但必须把 `torch/lib` 放进 **PATH**，`add_dll_directory` 不够），
  可质量并不更好（96.6% vs 97.8%）。详见 `docs/05-game.md` 的「已知差异」与「ONNX 可行性」两节。
- **参数在创建时定稿**：任务开跑后不再改 `job.params`，调参一律新建任务（`POST /api/jobs/{id}/analyze`）。
  这样每个任务目录的 `meta.json` 永远能解释自己的产物，也让「两个参数谁更好」变成两个目录的对比。
- **前端 `ACTIVE_STAGES` / `Stage` / `STAGE_TEXT` 必须跟着后端 `Stage` 一起改**：
  漏加新阶段会让任务在界面里「消失」且轮询停摆（`notes` 就踩过一次）。
- **`exports.smooth` 只平滑曲线**：有 `notes_game.json` 时音符的音高来自 GAME，
  平滑只改得动 `bend_midi` 与 USTX 的 pitd 曲线（`notes_midi` 字节级不变，2026-10-30 实测）。
  UI 文案也别再写「影响音符切分」——那只在没有 GAME 产物的启发式路径上成立。

## 当前阶段

主链路已跑通（2026-10-29，2026-10-30 复测）：上传 → 解码 0.1 s → 分轨 → FCPE 取音高 → 导出。
当前分支 `dev`，测试 **132 项通过**。GAME 权重 small / medium / large 都在本地，创建弹窗三档都可选
（`notes.model`）—— large 标「实验」：它在参考上更差（95.8%）、慢 3.6 s、吃 1 GB 显存，见 `docs/05-game.md` M0。GAME 已接入（`docs/05-game.md` M0–M3 完成）：
`python -m app.worker notes` 用 GAME 取边界与音高，188 s 样本上起音召回 97.6%、音高一致 96.5%（参考 751 音符）。

任务已按 `scope` 拆成「只分离 / 只分析 / 分离+分析」三条，前端对应两个入口与两张历史列表；
四个入口都已用真实素材跑过（A 只有 stems、AB 全链路、仅 B 无 stems、派生分析用父任务 vocals）。

**V2 已冻结**（2026-10-30 推送 `dev`）。V3 的方向与重构清单在 `docs/06-v3.md`，
其中已定：导出层不做平滑（`smooth = 1`，交给 OpenUtau）、曲线密度 50 ms → 25 ms。
动 V3 之前先读那一页，别在 V2 上继续加功能。

实测（12 s 素材）：

| 档位 | 分轨耗时 | 产物 | 音高 |
|---|---|---|---|
| `vocals`（默认） | 5.2 s | 只有 vocals.wav | 1202 帧，有声 87.9%，中位 350.6 Hz |
| `fast` | 4.3 s（热启动） | 四条轨 | — |
| `quality` | 约 4× 专家数 | 四条轨 | — |

导出：18 个音符 / `vocals.ustx` 23 KB / `vocals_bend.mid` 960 个弯音事件。

**档位的专家选择逻辑**：按「对目标音轨有非零权重的专家」筛，**不按下标**。取错专家会让 `totals[k]` 为 0、
除零后输出削波到满量程（踩过一次，peak/rms 都是 1.0）。weights 矩阵与完整推理见 `docs/01-decisions.md` D13。

**音高/音符导出是实验性功能**（UI 上标了「实验性」）。分轨是稳的，音高不是：

- FCPE 是**单声部**模型，合唱与和声只能出一条线
- 音符切分是**启发式**（半音游程 + 60 音分迟滞 + 120 ms 最短时长，
  外加 worker 里谱通量起音 `features/onset.json` 作为并集，见 `app/worker/onsets.py`），
  比不上 OpenUtau 的 GAME 那类音符级模型。断线（清辅音、气声）与边界误差都在所难免。
  起音只能抓「辅音起音」这一种证据：连音里同一个元音换字仍然切不开
- 切分是**完全划分**：非静音区间内音符首尾相接，只有静音才留缝。
  短片段并入邻居而不是丢弃——丢弃会在有声区里挖洞，音符会变成一小段一小段
- 想更准只有换模型：GAME 是训练出来的音符级模型，它的「边界阈值 / 边界半径 / 分数阈值」
  是模型输出的后处理参数，不是能移植的算法。实测数据与接入计划见 `docs/05-game.md`

**未做 / 未验证**：
- 导出固定 120 bpm（没做 BPM 检测，量化交给 DAW）
- USTX 尚未在真实 OpenUtau 里打开验证过，需要人工点一次
- 多人声/和声不做，一次只出一条曲线
- 整首 `quality` 档（四专家 × 全曲）没跑过，只用 12 s 素材验过四专家依次加载与释放
