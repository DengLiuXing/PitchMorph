# PitchMorph

本地声轨分离工具。把混音（或带音轨的视频）拖进浏览器，服务端用 **HT-Demucs FT 的 ONNX 权重**分出鼓 / 贝斯 / 其他 / 人声，页面上四轨同步试听、静音独奏、逐轨或打包下载。默认档只分出人声（单专家，最快），添加任务时可切到四轨档。

- 纯 ONNX Runtime 推理，**不安装 PyTorch**
- 视频 / 容器解码用**内置 ffmpeg**，无需单独安装
- 音频不出本机；单进程、无 Redis / Celery、前端免构建

> 本期只做分离。音高线提取与 OpenUtau 导入在后续里程碑（见文末）。

## 功能

- **任务列表**：像下载器一样管理多个任务——进行中 / 已完成 / 失败取消三组，带阶段、百分比与耗时
- **批量添加**：一次拖入多个文件排队处理（服务端串行）
- **重试**：取消或失败的任务直接重跑，复用已存的输入，不用重新上传
- **四轨工作台**：共享时间轴的波形、贯穿播放头、单轨静音 / 独奏 / 音量 / 下载

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python **3.11** | `demucs-onnx` 要求 ≥3.11，其 C 扩展依赖在 3.11 上最稳 |
| [uv](https://docs.astral.sh/uv/) | 用于创建虚拟环境与运行 |
| ffmpeg | **随依赖内置**（`imageio-ffmpeg` 提供静态二进制），无需单独安装 |
| Node **18+** | **仅构建前端时需要**。`app/static` 已提交构建产物，只跑服务不必安装 |

`uv sync` 会把 `imageio-ffmpeg` 一起装好，解码视频 / m4a 这类容器不需要你对系统做任何配置。探测顺序是：

```
PITCHMORPH_FFMPEG（手动指定）→ 内置 → 系统 PATH
```

内置版优先是为了跨机器行为一致；想用自己那份（比如更新或带特殊编码器）就设 `PITCHMORPH_FFMPEG`。

## 支持的输入

| 类别 | 格式 | 解码方式 |
|---|---|---|
| 音频 | wav / flac / mp3 / ogg / aiff | libsndfile（不经过 ffmpeg） |
| 容器 | mp4 / m4a / **m4s** / mov / mkv / webm / avi / ts / flv / 3gp / wmv / aac / opus | ffmpeg，**只取第一条音轨**，画面直接丢弃 |

有 ffmpeg 时不再按扩展名白名单拦截：任何能解封装的容器都放行，读不出来时返回 ffmpeg 的具体原因。

> `.m4s` 只是分片 MP4 的命名习惯。浏览器缓存目录里常见 `video.m4s` 和 `audio.m4s` 两个文件——拖音频那段，视频那段没有音轨会直接被拒。

## 启动

```powershell
uv sync
uv run uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000>。

就这两条，**不需要额外参数，也不需要自己判断装 GPU 还是 CPU**。

`uv sync` 装的是 `onnxruntime-gpu`，它自带 CPU 执行器。启动时会用一张最小计算图真探针一次 CUDA：

- 能用 → 走 GPU，顶栏芯片显示 `CUDA`
- 不能用 → 自动回落 CPU，芯片显示 `CPU 兜底`，悬停或环境自检里能看到原因

想确认本机卡在哪一步，跑诊断（只读，不改环境）：

```powershell
uv run python scripts/setup.py
```

### 让 CUDA 真正生效（Windows）

pip 上的 CUDA 运行库只发 Linux wheel（`nvidia-cublas` 没有 win_amd64），所以 Windows 需要系统装一次 [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads)：

- 版本 13.x 均可（驱动 617.42 对应 13.4），选 `exe (local)` 下载完整包
- 自定义安装**必须保留**：CUDA Runtime、CuBLAS、CuFFT、CuRAND、NVRTC —— ORT 分别链接 `cudart64_13` / `cublas64_13` / `cufft64_*` / `curand64_*` / `nvrtc64_*`，缺一个就起不来
- **可以取消**：Display Driver（别覆盖现有驱动）、Nsight、Visual Studio Integration、Documentation

cuDNN 不用手动装，`uv sync` 会从 pip 装好（`nvidia-cudnn-cu13` 有 win_amd64 wheel）。

CUDA 13 把 DLL 放在 `bin\x64`（安装器会把它写进系统 PATH）。即使 PATH 里没有，`app/runtime.py` 也会用 `os.add_dll_directory()` 主动注册——Python 3.8+ 已不再从 PATH 找 DLL，这一步是必要的保险。

判断依据是可加载的 `cudart64_13.dll`，不是「有没有 N 卡」。很多游戏本自带 PhysX 的 `cudart64_65.dll`（CUDA 6.5），那个 ONNX Runtime 用不了。

> **只装一个 onnxruntime**：`demucs-onnx` 硬依赖 CPU 版 `onnxruntime`，与 `onnxruntime-gpu` 同模块名、不能共存。`pyproject.toml` 用 `[tool.uv] override-dependencies` 把那条声明改写成「仅 macOS 生效」，所以 Windows/Linux 上永远只装 GPU 版——`uv sync` 和 `uv run` 都不会把 CPU 包带回来，启动命令因此和其它项目一样。

### 前端构建

`app/static` 里已提交构建产物，只跑服务不需要 Node。改前端时才需要：

```powershell
cd frontend
npm install
npm run build      # 输出到 ../app/static
npm run dev        # 开发模式，5173 端口，/api 与 /media 代理到 8000
```

首次选择某一档位时会下载对应权重（缓存在 `data/models`），下载完成后再分轨就不再联网。

### 先单独把权重下好（推荐）

下载进度不跟任务轮询绑在一起，也可以断点续传：

```powershell
uv run python -m app.weights --list      # 看需要哪些文件
uv run python -m app.weights vocals      # 166 MB，只下四轨档里的人声专家
uv run python -m app.weights quality     # 660 MB，四轨质量档（已包含 vocals）
uv run python -m app.weights fast        # 166 MB，四轨快速档的单文件模型
```

下载卡住时按这个顺序试：

```powershell
$env:HF_HUB_DISABLE_XET = "1"                    # 关掉 Xet 传输
$env:HF_ENDPOINT = "https://hf-mirror.com"       # 换国内镜像
uv run python -m app.weights vocals
```

下完再启动服务，界面里的档位会直接显示「已缓存」。

| 档位 | 模型 | 权重体积 | 3 分钟歌曲的预计耗时 |
|---|---|---|---|
| 仅人声（默认） | `htdemucs_ft_vocals` | 166 MB | 1–2 分钟 |
| 四轨 · 质量优先 | `htdemucs_ft` 四专家集成 | 660 MB | 2–5 分钟 |
| 四轨 · 快速 | `htdemucs` 单文件 | 166 MB | 1–3 分钟 |

体积与耗时都是 CPU 上的量级参考；耗时随机器核数与曲目长度变化。

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `PITCHMORPH_DATA_DIR` | `./data` | 任务目录与权重缓存的根 |
| `PITCHMORPH_FFMPEG` | 内置 → 系统 PATH | 指定 ffmpeg 可执行文件路径，优先于内置版 |
| `PITCHMORPH_PROVIDERS` | 自动探测 | 传给 ONNX Runtime 的执行器，逗号分隔。不设时启动会**真探针**一次 CUDA（建一个最小 session 并试跑），可用则用 GPU，否则回落 CPU；显式设置则无条件优先，可用来强制 CPU 或强制 GPU |
| `PITCHMORPH_PARALLEL` | `1` | 四轨集成档同时运行的专家数（1–4）。实测 16 核机器上 2 并发**更慢**（60 s 片段 85 s → 143 s）且内存涨到约 10 GB，所以默认 1；核数更多的机器可自行上调，见 [docs/perf-audit.md](docs/perf-audit.md) |
| `HF_ENDPOINT` | 官方源 | 国内网络可设为 `https://hf-mirror.com` |

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
uv run uvicorn app.main:app --port 8000
```

## 使用限制

- 单文件 ≤ **300 MB**，提取出的音频 ≤ **12 分钟**（上限按音频算，画面不计入）；容器类文件先读头部时长，超限直接拒绝、不做转码
- 输出固定为 **44.1 kHz / 16-bit 立体声 WAV**；单轨下载名为 `<源文件名>-<stem>.wav`，多个任务之间不会重名
- 串行处理，**同时最多 3 个任务处于活动状态**（1 个在跑，其余排队），超出直接拒绝
- 任务列表是服务端状态，刷新页面不会丢；但**服务重启后列表会清空**（磁盘目录仍保留到 TTL 到期）
- 取消的生效粒度是**一个 7.8 秒的分块**（7.8 s @ 44.1 kHz 是 ONNX 图的固定输入长度），实测延迟 1–10 秒；权重下载阶段取消则立即生效
- 任务目录保留 24 小时后自动清理

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/jobs` | 上传（multipart：`file` + `tier` + `parallel`），立即返回 `job_id` |
| `GET` | `/api/jobs` | 任务列表（新 → 旧），页面靠它恢复整张列表 |
| `GET` | `/api/jobs/{id}` | 单个任务阶段、进度、已产出轨道 |
| `POST` | `/api/jobs/{id}/cancel` | 请求取消（7.8 秒分块边界生效） |
| `POST` | `/api/jobs/{id}/retry` | 用已存的 `input.wav` 重跑，不用重新上传 |
| `DELETE` | `/api/jobs/{id}` | 删除任务与目录 |
| `GET` | `/api/jobs/{id}/download` | 打包下载全部轨道（`<源文件名>-stems.zip`，内含同名目录） |
| `GET` | `/api/models` | 档位元数据与权重缓存状态 |
| `GET` | `/api/health` | 执行器、ffmpeg、权重目录、支持格式 |

分轨音频通过 `/media/{id}/stems/{stem}.wav` 静态提供（`/media` 挂载在 `data/jobs`，支持 Range，浏览器可拖动定位）。

## 目录结构

```
app/
├── config.py      # 路径、上限、档位、ffmpeg 三级探测
├── runtime.py     # 执行器选择：CUDA 真探针、CPU 兜底、会话常驻与预热
├── separator.py   # 解码规整 + 自管分块推理（唯一的 demucs-onnx 接触点）
├── jobs.py        # 内存任务表 + 单 worker 队列 + 取消 + TTL 清理
├── schemas.py     # 接口数据结构
├── main.py        # FastAPI 路由与静态挂载
├── weights.py     # 权重预下载 CLI（python -m app.weights）
└── static/        # 前端构建产物（由 frontend/ 生成，入库，勿手改）
frontend/
├── vite.config.ts # base '/static/'，build 输出到 ../app/static
└── src/           # Vue 3 + TS：App.vue、components/、api/、styles/
scripts/
├── setup.py          # 诊断 CUDA 可用性；--apply 等同 uv sync
├── bench.py          # 端到端计时，拆解码/加载/推理/写盘
├── smoke_offline.py  # 分轨流程回归，不需要权重
└── smoke_media.py    # 容器格式回归（mp4 / m4s / 无音轨 / 超长）
data/
├── jobs/<id>/     # input.wav、stems/*.wav、package.zip
└── models/        # ONNX 权重缓存
```

## 后续里程碑

- **M2 · 音高线提取**：对 `stems/vocals.wav` 做 f0 估计与音符切分，产出可编辑的音高曲线。实现时新增 `features/` 目录（与 `stems/` 平级），不改动现有接口。
- **M3 · 导入 OpenUtau**：输出 `.ustx`（OpenUtau 原生工程格式，`notes` + `curves`）或配套 MIDI，让音高线直接落到工程里。

## 开发文档

架构细节、为什么绕开 `demucs_onnx.separate()`、调试命令和踩过的坑，见 [AGENTS.md](AGENTS.md)。
