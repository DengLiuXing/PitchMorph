---
name: PitchMorph 轻量扒谱工具（分轨优先）
overview: 从空目录搭建一个本地 Web 工具：上传音频后用 demucs-onnx（纯 onnxruntime，无 PyTorch）跑 htdemucs_ft 分出鼓/贝斯/其他/人声四轨，前端可试听与下载；转谱作为第二阶段接入，保留接口。
design:
  architecture:
    framework: html
  styleKeywords:
    - 深色音频工作站
    - 波形可视化
    - 克制的玻璃拟态
    - 琥珀强调色
    - 等宽数字
    - 紧凑高密度控制
  fontSystem:
    fontFamily: PingFang SC
    heading:
      size: 28px
      weight: 600
    subheading:
      size: 15px
      weight: 500
    body:
      size: 14px
      weight: 400
  colorSystem:
    primary:
      - "#F2A93B"
      - "#FFC46B"
      - "#2DD4BF"
    background:
      - "#0A0C10"
      - "#12161F"
      - "#1A2030"
    text:
      - "#E8ECF1"
      - "#8A94A6"
      - "#5A6373"
    functional:
      - "#2DD4BF"
      - "#F87171"
      - "#FBBF24"
      - "#3B82F6"
todos:
  - id: init-env
    content: 用 uv 初始化工程并固定 Python 3.11，装齐 fastapi、uvicorn、python-multipart、demucs-onnx==0.3.4，跑通 demucs-onnx list-models
    status: pending
  - id: separator-core
    content: 实现 app/separator.py：按 stem 逐个推理、进度回调、取消检查点、44.1k 立体声规整与 HF 镜像兜底
    status: pending
    dependencies:
      - init-env
  - id: job-api
    content: 实现 app/config.py、schemas.py、jobs.py、main.py：单 worker 队列、上传/状态/取消/zip 下载、/media 静态挂载、24h 清理
    status: pending
    dependencies:
      - init-env
  - id: web-shell
    content: 用 [skill:frontend-design] 完成上传态与处理态界面，含模型档位切换、阶段指示、轮询与取消文案
    status: pending
    dependencies:
      - job-api
  - id: stem-workbench
    content: 用 [skill:frontend-design] 完成四轨同步播放工作台：canvas 波形、播放头、静音/独奏、音量、逐轨与打包下载
    status: pending
    dependencies:
      - job-api
      - web-shell
  - id: verify-harden
    content: 端到端验证一首真实流行歌曲的四轨分离与同步试听，补齐错误提示、内存与清理策略、README（含 M2 转谱下一步）
    status: pending
    dependencies:
      - separator-core
      - job-api
      - stem-workbench
---

## 用户需求

用户要做一个**轻量级扒谱工具**，但明确指出第一步是「先分离各个声轨」。经确认，明确了三条约束：

- **技术路线**：使用 `htdemucs-ft` 的 ONNX 导出模型（`htdemucs-ft-vocals-onnx` 这一系产物），即**走 ONNX Runtime、不引入 PyTorch**，纯 CPU 可跑；本机此前已放弃 CUDA/DirectML 加速路线，因此 GPU 只能作为可选项，不能阻塞启动。
- **交付形态**：本地 Web UI（FastAPI + 前端），能上传音频、播放分轨、下载分轨。
- **扒谱目标**：流行乐队编制（鼓、贝斯、吉他、人声）+ 人声旋律/和声。

## 产品概述

一个在本机运行的单页 Web 工具：把一首完整混音拖进浏览器，服务器用 HT-Demucs FT 的 ONNX 权重分离出鼓、贝斯、其他、人声四轨，页面上以多轨工作台的形式同步试听、单独静音/独奏、逐轨下载。首期只做「分轨闭环」，为后续接入转谱（audio → MIDI）预留清晰的数据接缝。

## 核心功能

- **音频上传**：拖拽或点击上传，支持 mp3/wav/flac/m4a，展示文件名、时长、大小，超限时给出明确提示。
- **分轨参数选择**：三档模型选项——`htdemucs_ft` 四轨（质量优先，默认）、`htdemucs_ft_vocals` 仅人声（速度优先，省约 4 倍算力）、`htdemucs` 四轨（快速档）。
- **后台任务与进度**：服务端串行排队处理，前端轮询显示阶段（下载模型 → 加载 → 分离 drums/bass/other/vocals）与耗时，支持取消。
- **多轨工作台**：四条轨道各带波形、静音/独奏、音量、单独下载；顶部统一播放条控制播放/暂停/进度拖动/循环区间，四轨严格对齐。
- **结果导出**：一键打包下载全部 WAV（44.1 kHz 立体声）。
- **运行环境自检**：页面可查看 onnxruntime 可用执行器、ffmpeg 是否存在、模型是否已下载。

## 视觉预期

深色、专业音频工作站质感：炭黑底 + 琥珀色强调，波形是画面主角，控制条紧凑，处理中有明确的阶段性反馈，不做营销页式的花哨排版。


## 技术栈选择

| 层 | 选择 | 理由 |
|---|---|---|
| 运行时 | **Python 3.11**（`uv python pin 3.11`，本机已有 uv 管理的 3.11.15） | `demucs-onnx` 需 ≥3.11；默认的 3.14 存在 onnxruntime wheel 风险；3.11 也是后续 `basic-pitch` 的上限，取唯一交集 |
| 分轨推理 | `demucs-onnx==0.3.4`（MIT），核心依赖仅 `onnxruntime` + `numpy` + `soundfile` + `soxr` + `tqdm` + `huggingface-hub` | 纯 numpy/ORT 推理，**无需 PyTorch**；模型自动从 HF `StemSplitio` 拉取 |
| 服务端 | FastAPI + uvicorn | 单进程即可，静态托管方便 |
| 任务调度 | 进程内 `ThreadPoolExecutor(max_workers=1)` + 内存 Job 表 | 不引 Celery/Redis，符合「轻量」；单 worker 同时把峰值内存锁在约 1.5 GB |
| 前端 | 免构建静态页（HTML + ES Module + 手写 CSS）+ Web Audio API | 本地工具无需 npm/Vite 工具链，离线可直接跑；避免 CDN 依赖 |

**权重档位**：一律使用 `_fp16weights` 变体（体积减半，运行时按 fp32 计算，与 fp32 精度差约 6e-5）。`htdemucs_ft` 全 bag = 660 MB；`htdemucs_ft_vocals` 单专家 = 166 MB；`htdemucs` 单文件 = 166 MB。

## 实现方案

### 总体策略

1. **分轨调用按 stem 逐个执行**，而不是一次跑完 4 轨：`htdemucs_ft` 本身就是「每个源一个微调专家」的 bag，单 stem 调用与整 bag 在该轨上等价，但带来三个收益——① 进度可从 0/25/50/75/100 自然上报；② 峰值内存只驻留 1 个 session（约 600 MB），而不是 4 个；③ 每个 stem 之间是天然的取消检查点。
2. **媒体走 StaticFiles 挂载**，而不是逐个写 FileResponse：Starlette 的静态文件服务原生支持 Range 请求，`<audio>` 的拖动定位才能正常工作，代码量也最少。
3. **服务端只做「排队 + 状态」，不做同步长任务**：POST 立即返回 job_id，前端 1s 轮询；处理器里任何异常都写进 job.error 并保留可读的 traceback 摘要。
4. **模型下载与推理分离**：启动时只探测本地是否已有权重，不主动下载；首次提交任务时才下载，并在 UI 上明示「首次需下载约 660 MB」。支持 `HF_ENDPOINT=https://hf-mirror.com` 与手动放置权重的目录兜底。

### 关键决策与权衡

- **不做 GPU 自动探测开关**：保留 `PITCHMORPH_PROVIDERS` 环境变量透传给 `onnxruntime`，默认 `CPUExecutionProvider`。理由：本机未装 CUDA 工具包，自动探测一旦走错会以难以理解的 ORT 报错终止，而不只是变慢。
- **不做实时进度百分比**：`demucs-onnx` 的 Python API 没有逐帧回调，只有 tqdm 日志。因此进度按「阶段 + 已完成 stem 数」表达，配合已耗时显示，比伪造百分比更诚实。
- **首次运行不做模型预下载**：预下载会让 `uv run` 首次启动挂几分钟且无法取消；改为任务级下载并复用 HF 缓存。
- **单 worker 串行**：换并行能提速，但 4 个 session 并发会让内存跳到 4–6 GB 并显著拖慢单曲，与「轻量」目标相悖。队列上限设为 3，超出直接拒绝。

### 性能与可靠性

- 3 分钟歌曲在普通 Windows CPU 上四轨预计 **2–5 分钟**（参考 M4 Pro CPU EP 约 88s，RTF≈0.49，桌面 x86 通常更慢）。任务化 + 进度 + 取消是必需项，不是优化项。
- 输入统一在送模型前规整为 44.1 kHz 立体声（`soxr` 一次重采样），避免 `soundfile` 处理异常格式失败；超过 12 分钟或 100 MB 直接拒绝并提示分段。
- `onnxruntime` 设 `intra_op_num_threads = min(8, os.cpu_count())`，避免占满全部核心导致页面卡顿。
- 磁盘：`data/jobs/{job_id}/` 下放 `input.wav` 与 `stems/*.wav`，启动时与每小时清理超过 24h 的目录；删除任务时同步删目录。
- 安全：job_id 用 uuid4；所有文件访问只经 `StaticFiles` 挂载的 `/media` 前缀，不接受用户传入路径；上传文件名仅用于展示，落盘一律用 uuid。

## 架构设计

```mermaid
graph LR
    A[Browser SPA] -->|POST /api/jobs multipart| B[FastAPI]
    A -->|GET /api/jobs/id 轮询| B
    A -->|Range 流式| C[/media StaticFiles/]
    B --> D[JobRegistry + ThreadPool(1)]
    D --> E[separator.py]
    E --> F[(HF 权重缓存)]
    E --> G[onnxruntime CPU EP]
    E --> C
```

数据流：上传 → 落盘 `input.wav` → 入队 →（按 stem 顺序）加载 session → 推理写 `stems/{name}.wav` → 更新 job.stems → 前端切换到工作台读取 `/media/jobs/{id}/stems/*.wav`。

## 目录结构

```
PitchMorph/
├── pyproject.toml                    # [NEW] uv 工程声明；requires-python = ">=3.11,<3.12"；依赖 fastapi、uvicorn[standard]、python-multipart、demucs-onnx==0.3.4
├── .python-version                   # [NEW] 内容为 3.11，确保 uv 使用已安装的 3.11.15 而非默认 3.14
├── .gitignore                        # [NEW] 忽略 .venv/、data/、__pycache__/
├── README.md                         # [NEW] 启动方式、首次下载说明、HF 镜像配置、模型档位说明、M2 转谱选型与验证步骤
└── app/
    ├── __init__.py                   # [NEW] 空包标记
    ├── config.py                     # [NEW] 路径常量（DATA_DIR/STEMS_DIR）、上传上限（100MB/12min）、默认模型与可用模型清单、providers 与线程数、请求体大小限制常量
    ├── schemas.py                    # [NEW] Pydantic 模型：JobCreateResponse、JobStatusResponse（status/stage/progress/elapsed/stems/error）、ModelInfo、HealthResponse
    ├── separator.py                  # [NEW] 分轨核心。封装 demucs-onnx：模型下载状态探测、按 stem 逐个推理、on_progress 回调、should_cancel 检查点、统一输出 44.1k 立体声 WAV；不暴露任何 ORT 细节给上层
    ├── jobs.py                       # [NEW] 内存 Job 表 + ThreadPoolExecutor(max_workers=1) + 队列上限 + 定时清理（24h）+ 取消标志；线程安全用 Lock
    ├── main.py                       # [NEW] FastAPI 实例、CORS 不需要、挂载 /media StaticFiles、路由：POST /api/jobs、GET /api/jobs/{id}、DELETE /api/jobs/{id}、GET /api/jobs/{id}/download（zip）、GET /api/models、GET /api/health、GET / 返回 index.html
    └── static/
        ├── index.html                # [NEW] 单页三段式结构：上传态 / 处理态 / 工作台态，含模板与无障碍标签
        ├── style.css                 # [NEW] 深色工作站主题：CSS 变量（色板/间距/圆角）、波形容器、轨道条、传输控制条、进度卡；含窄屏折叠
        └── app.js                    # [NEW] 状态机（idle→uploading→processing→ready→error）、轮询、Web Audio 同步播放（单一 AudioContext + 4 个 MediaElementSource + 主时钟对齐）、波形绘制（canvas 读峰值数组）、静音/独奏/音量、下载
```

## 关键代码结构

```python
# app/schemas.py
class JobStage(str, Enum):
    QUEUED = "queued"; DOWNLOADING = "downloading"; LOADING = "loading"
    SEPARATING = "separating"; DONE = "done"; CANCELED = "canceled"; ERROR = "error"

class JobStatusResponse(BaseModel):
    id: str
    stage: JobStage
    progress: float          # 0..1，按已完成 stem 数推进
    stem: str | None         # 当前正在处理的 stem
    elapsed_sec: float
    stems: dict[str, str]    # {"drums": "/media/jobs/<id>/stems/drums.wav", ...}
    error: str | None
```

```python
# app/separator.py
def separate_stems(
    src: Path, out_dir: Path, *,
    model: str = "htdemucs_ft",
    stems: tuple[str, ...] = ("drums", "bass", "other", "vocals"),
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Path]:
    """按 stem 逐个推理并把 WAV 写入 out_dir。
    每个 stem 开始前调用 should_cancel()，为真则抛 Canceled 并清理半成品。
    内部只用 demucs-onnx 的公开 API 与 onnxruntime InferenceSession，不依赖 torch。
    """
```

## 实施注意事项

- **不要安装 `demucs-onnx[export]`**：它会拉 `torch>=2.4,<2.5` + `demucs==4.0.1`，与「无 PyTorch」目标直接冲突。需要 MP3 输出时才加 `[mp3]`；本方案输出 WAV，不需要。
- 首次调用前先 `uv run demucs-onnx list-models` 确认模型别名与版本一致，再写死别名，避免猜错字符串。
- 推理前把音频规整一次为 44.1k/立体声 WAV，后续 4 次调用复用同一文件，避免重复解码与重采样。
- `StaticFiles` 必须设置 `follow_symlink=False` 且挂载点固定在 `data/jobs`，不要暴露 `data` 根目录。
- 日志用标准 `logging`，INFO 记录阶段流转（job_id/模型/时长/stem/耗时），ERROR 记录 traceback；**不打印音频采样数据与用户目录绝对路径**。
- 取消采用 `threading.Event`，只在 stem 边界生效（子进程内无法中断 ORT），需在 UI 上说明「将在当前轨结束后停止」，避免用户误以为卡死。



## 设计风格

以「深夜录音棚 + 现代音频工作站」为基调的深色界面：近黑底色让波形成为视觉主体，琥珀色作为唯一的高饱和强调色（播放头、进行中的进度、主按钮），青绿色仅用于「完成/就绪」状态。面板使用极细的 1px 边框与低透明度分层阴影，而非大面积卡片阴影，避免廉价的「渐变卡片堆叠」感。数字（时间轴、时长、dB、耗时）统一用等宽数字字形，保证跳动时不抖动。

## 页面规划（单页三态，共 1 屏）

**Block 1 · 顶栏**：左侧波形图标 + PitchMorph 字标，中间是环境徽标（CPU EP / 模型已缓存），右侧一个「环境自检」图标按钮，点击弹出 onnxruntime providers、ffmpeg 版本、权重路径的小面板。

**Block 2 · 上传态主区**：占据视口中部的虚线拖拽区，中央为大号提示「拖入音频」+ 支持格式说明；拖入时边框转为琥珀色并轻微内发光。下方一行紧凑参数条：模型档位三段式切换（质量 / 仅人声 / 快速），每档右侧以弱化小字标注「660MB · 约 3 分钟」这类预期成本，选择后立即反映到主按钮文案。

**Block 3 · 处理态**：拖拽区原位替换为任务卡——文件信息行、四段式阶段指示（下载 → 加载 → 分离 → 完成）以细线连接，当前段琥珀高亮并带流动光效；下方显示「正在分离 bass · 2/4 · 已用 01:12」，取消按钮为次要样式，点击后转为「将在当前轨结束后停止」。

**Block 4 · 多轨工作台**：四条等高轨道纵向堆叠，左侧固定 96px 为轨头（名称、静音 M / 独奏 S 圆钮、音量滑杆、下载图标），右侧为 canvas 波形，共享同一时间刻度；播放位置有一条贯穿四轨的琥珀色播放头。轨道悬停时整行背景上浮一级，静音态波形降为 30% 透明度，独奏时其余轨道同步变暗。

**Block 5 · 传输控制条**：吸附在工作台底部，包含播放/暂停圆钮（44px，琥珀实心）、当前时间/总时长等宽数字、可拖动的进度条（悬停放大热区）、循环区间开关、以及右侧「下载全部」主按钮。

## 交互与动效

- 播放头与进度条共用 60fps 的 `requestAnimationFrame` 更新，避免 setInterval 抖动。
- 波形采用双色绘制：已播放段为不透明琥珀，未播放段为低透明度灰白，实现「播放扫过」的视觉效果。
- 状态切换用 180–240ms 的位移 + 透明度过渡（上传态卡片向下淡出，工作台从下方 12px 上移淡入）。
- 所有可点击元素有 `:focus-visible` 琥珀色描边；无数据时波形区显示骨架式静默条纹而非空白。
- 窄屏（<760px）时轨头收缩为图标列，音量滑杆移入弹出层，传输条保持吸底。

## 视觉细节

- 面板用 `backdrop-filter: blur(12px)` + 半透明深色叠加在极淡的径向光晕背景上，形成克制的玻璃感。
- 进度条轨道 4px 圆角，填充部分带 8px 柔光；播放头为 2px 竖线加顶部小三角。
- 空状态与错误态共用一个信息区，错误使用柔和红（非刺眼正红）+ 可复制的错误码。


## Agent Extensions

### Skill
- **frontend-design**
  - Purpose: 生成上传态、处理态、四轨工作台三套界面的实际代码（HTML/CSS/Canvas 波形与动效），避免千篇一律的 AI 模板化观感。
  - Expected outcome: 产出可直接被 FastAPI 托管、在深色工作站风格下视觉完整且交互可用的 `index.html` / `style.css` / `app.js`，包含播放头扫过、波形双色绘制、静音/独奏变暗等细节。
