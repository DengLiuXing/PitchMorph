# PitchMorph v2

本地 Web 工具：**一首混音进，四条音轨 + 人声音高线 + MIDI / OpenUtau 工程出**。

推理全在本机（Demucs 分轨 + FCPE 取音高），音频不出机器。浏览器只负责播放与可视化。

> **分轨是稳的，音高/音符导出是实验性的。** FCPE 是单声部模型（合唱只能出一条线），
> 音符切分是启发式的（半音游程 + 迟滞），精度比不上 OpenUtau 的 GAME 那类音符级模型。
> 断线（清辅音、气声）与音符边界误差都在所难免，把它当「初稿」而不是「成品」。

## 安装

需要 NVIDIA 显卡（本机是 RTX 5060 Laptop / sm_120）、[uv](https://docs.astral.sh/uv/)、Node 18+。

```powershell
uv sync                      # Python 3.11 + torch cu128，约 2.5 GB
cd frontend; npm install; npm run build; cd ..
```

`npm run build` 会把前端产物写进 `app/static/`，FastAPI 直接托管。

## 启动

```powershell
uv run uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000>。

**不要加 `--reload`**：任务表在内存里，任何 `.py` 改动都会重启进程、丢掉进行中的任务。

## 用法

1. 拖入 mp3 / flac / wav / mp4 / m4s（容器只取第一条音轨）
2. 选档位（默认「人声」）
3. 等任务变成「已完成」，点「打开工作台」

| 档位 | 跑什么 | 产物 | 12 s 素材实测 |
|---|---|---|---|
| **人声**（默认） | `htdemucs_ft` 里专门负责人声的那一个专家 | 只有 `vocals.wav` | 分轨 5.2 s |
| 四轨 · 快 | `htdemucs` 单模型 | 四条轨 | 分轨 4.3 s |
| 四轨 · 质量 | `htdemucs_ft` 四个专家集成 | 四条轨 | 约 4× |

「人声」档只跑 1/4 的专家，因为 `htdemucs_ft` 的四个专家各自只负责一个音源
（weights 是对角阵）。做音高分析时选它就够了，另外三条轨本来也用不上。

工作台里可以四轨同步播放、静音/独奏、点波形定位、看人声音高线（220–880 Hz，对数纵轴）。

## 导出

右上角「导出」。**下面三件都是实验性产物**（导出面板里可选平滑度）：

| 产物 | 用途 |
|---|---|
| `vocals_notes.mid` | 离散音符，任何 DAW 都能读 |
| `vocals_bend.mid` | 长音符 + 每 10 ms 一个弯音事件，还原原始曲线 |
| `vocals.ustx` | OpenUtau 工程，音符自带完整音高曲线 |

分轨的四条 WAV 也可以单独下载。

### 在 OpenUtau 里打开 USTX

导出的工程**没有指定音源**（`singer` 留空），所以：

1. 打开 `.ustx` 后，音符会标红——这是正常的
2. 在轨道上右键 → 选择你自己的音源（音源需要你先装好）
3. 之后就能正常播放

没有音源时音高线仍然完整可见，可以先确认曲线对不对。

## 限制

- 单机、单用户、单任务排队（并发跑两个模型会让 8 GB 显卡 OOM）
- 单个文件 ≤ 300 MB，时长 ≤ 12 分钟
- 音高只对人声轨做，一次一条曲线（不做多声部分离）
- 音符切分是启发式的，达不到 GAME 这类音符级模型的精度；想更准只能换模型
- 任务产物保留 7 天，进程重启后会从磁盘上的 `meta.json` 恢复任务列表

## 目录

```
app/            后端（FastAPI + 子进程编排）
app/domain/     纯函数：音高曲线 → 音符 → MIDI / USTX，不 import torch
app/worker/     子进程，唯一 import torch 的地方
frontend/       Vue 3 + TS + Vite，构建到 app/static
data/jobs/{id}/ input.wav · stems/ · features/ · exports/
docs/           设计文档与实测记录
```

开发约定见 [AGENTS.md](AGENTS.md)。
