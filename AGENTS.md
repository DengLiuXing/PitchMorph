# AGENTS.md — PitchMorph v2

本地 Web 工具：一首混音进，四轨 WAV + 人声音高线 + MIDI + OpenUtau 工程出。
FastAPI 主进程**不加载 torch**；全部分轨与音高推理发生在 `python -m app.worker <stage>` 子进程里。

## 文档地图

| 文件 | 内容 |
|---|---|
| `docs/03-pytorch-dual.md` | 为什么从 ONNX 换到 PyTorch |
| `docs/04-ustx.md` | USTX 字段契约（对照 OpenUtau 源码核实） |
| `docs/01-decisions.md` | 关键决策与各自的推翻条件 |
| `docs/perf-audit.md` | **ONNX 时代的数据，v2 不适用**，只作历史参考 |
| `.agent/memory/` | 跨会话项目记忆，索引是 `MEMORY.md`（随仓库走，不要 ignore） |

## 构建与测试

普通构建与测试**直接跑**，跑完汇报结果。只有会下载几百 MB 或必须走代理的操作才停下来等用户（torch wheel、demucs / FCPE 权重）。删除文件仍要先说清路径与理由。

```powershell
uv sync                                  # 装依赖（torch cu128，约 2.5 GB，属于大文件）
uv run python scripts/probe_env.py       # 探针：sm_120 + FCPE 可用性
uv run pytest                            # 全部测试，不需要 GPU
cd frontend; npm run build; cd ..        # 前端构建，产物写 app/static（入库，勿手改）
uv run uvicorn app.main:app --port 8000  # 启动（不要加 --reload）
```

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
app/main         ← 路由 + /media 静态托管
app/domain       ← 纯函数：curve / notes / midi / ustx，不 import torch、不做 IO
app/worker       ← 只在子进程被 import，全项目唯一 import torch 的地方
```

依赖只能向下。`domain/` 与 `worker/` 互不 import，靠 `features/*.json` 解耦。

## 子进程协议

stdin 一份 JSON 任务描述；stdout 逐行一个 JSON 事件；**日志一律走 stderr**。

```
{"t":"progress","frac":0.42,"detail":"segment 3/7"}
{"t":"done","artifacts":{"vocals":"stems/vocals.wav"}}
{"t":"error","message":"CUDA out of memory"}
```

取消 = `terminate()`。父进程不解析模型的任何内部状态，这是「换模型不动业务层」的边界。

## 磁盘契约

```
data/jobs/{id}/
├── input.wav                 # 44.1k 立体声 PCM16，retry 依赖它
├── stems/{drums,bass,other,vocals}.wav
├── features/{pitch,notes}.json
└── exports/*.mid, *.ustx     # 派生产物，可随时删除重建
```

`features/` 与 `stems/` 平级：音高依赖 `stems/vocals.wav` 的稳定路径。

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

## 当前阶段

主链路已跑通（2026-10-29）：上传 → 解码 0.1 s → 分轨 → FCPE 取音高 → 导出。

实测（12 s 素材）：

| 档位 | 分轨耗时 | 产物 | 音高 |
|---|---|---|---|
| `vocals`（默认） | 5.2 s | 只有 vocals.wav | 1202 帧，有声 87.9%，中位 350.6 Hz |
| `fast` | 4.3 s（热启动） | 四条轨 | — |
| `quality` | 约 4× 专家数 | 四条轨 | — |

导出：18 个音符 / `vocals.ustx` 23 KB / `vocals_bend.mid` 960 个弯音事件。74 项测试通过。

**档位的专家选择逻辑**：`htdemucs_ft` 的 `weights` 是对角阵（`models[0]`→drums、`models[3]`→vocals），
`separate.py` 按「对目标音轨有非零权重的专家」筛，不按下标。取错专家会让 `totals[k]` 为 0、
除零后输出削波到满量程（踩过一次，peak/rms 都是 1.0）。

**音高/音符导出是实验性功能**（UI 上标了「实验性」）。分轨是稳的，音高不是：

- FCPE 是**单声部**模型，合唱与和声只能出一条线
- 音符切分是**启发式**（半音游程 + 60 音分迟滞 + 120 ms 最短时长），
  比不上 OpenUtau 的 GAME 那类音符级模型。断线（清辅音、气声）与边界误差都在所难免
- 想更准只有换模型：GAME 是训练出来的音符级模型，它的「边界阈值 / 边界半径 / 分数阈值」
  是模型输出的后处理参数，不是能移植的算法

**未做 / 未验证**：
- 导出固定 120 bpm（没做 BPM 检测，量化交给 DAW）
- USTX 尚未在真实 OpenUtau 里打开验证过，需要人工点一次
- 多人声/和声不做，一次只出一条曲线
- 整首 `quality` 档（四专家 × 全曲）没跑过，只用 12 s 素材验过四专家依次加载与释放
