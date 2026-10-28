# AGENTS.md — PitchMorph 开发指引

本地声轨分离 Web 工具：FastAPI 后端 + 免构建静态前端，把整首混音分成鼓 / 贝斯 / 其他 / 人声四轨。推理走 `demucs-onnx`（纯 ONNX Runtime，**不装 PyTorch**），容器解码走内置 ffmpeg。

## 常用命令

```powershell
uv sync                                  # 创建 3.11 环境并装依赖（含内置 ffmpeg）
uv run uvicorn app.main:app --port 8000  # 启动；调代码时加 --reload（注意见下文）
```

回归验证（没有 pytest，这两个脚本就是测试集）：

```powershell
uv run python scripts/smoke_offline.py   # 分轨流程：四专家 / 单文件 / 取消清理，stub 掉 ONNX，不需要权重
uv run python scripts/smoke_media.py     # 容器格式：mp4 / m4s / 无音轨 / 超长，现场生成样本
cd frontend; npm run typecheck           # 前端类型检查（vue-tsc --noEmit）
cd frontend; npm run build               # 构建产物写入 app/static
```

单个用例目前的隔离方式就是打开脚本、注释掉不需要的 fixture，脚本本身不接受参数。

权重与模型：

```powershell
uv run python -m app.weights --list      # 列出各档位需要哪些 ONNX 文件
uv run python -m app.weights vocals      # 预下载：quality | vocals | fast | all
uv run demucs-onnx list-models           # 核对模型别名，别凭文档猜字符串
```

下载卡住时：`$env:HF_HUB_DISABLE_XET="1"`、`$env:HF_ENDPOINT="https://hf-mirror.com"`，重跑同一条命令可续传。

## 架构

### 一次请求的完整路径

```
POST /api/jobs (multipart: file + tier + parallel)
  └─ main.create_job
       ├─ config.can_accept(suffix)          # 有 ffmpeg 就不按白名单拦
       ├─ 流式落盘 data/jobs/{id}/source.*   # 边写边卡 300 MB 上限
       ├─ separator.probe_with_ffmpeg()      # 仅容器类：读头部拿时长 + 是否有音轨
       ├─ separator.decode_to_wav()          # → data/jobs/{id}/input.wav（44.1k / 立体声 / PCM16）
       └─ jobs.registry.submit()             # 入队，立即 202 + job_id

GET /api/jobs  ← 前端 900ms 轮询（只在有活跃任务时）
DELETE /api/jobs/{id}、POST /{id}/cancel、POST /{id}/retry、GET /{id}/download
```

`input.wav` 解码后保留在任务目录里，`retry` 接口直接复制它重跑，不需要重新上传。

### 解码层：两条路，永不交叉

`separator.decode_to_wav` 先试 `soundfile`（libsndfile，覆盖 wav/flac/mp3/ogg/aiff），失败才调 ffmpeg。ffmpeg 的参数是 `-map 0:a:0 -c:a pcm_s16le`——**明确只取第一条音轨**，视频画面整段丢弃。这条规则让 mp4 / m4s / mkv / ts 全部走同一条代码路径。

ffmpeg 的来源由 `config.resolve_ffmpeg()` 决定，顺序是 `PITCHMORPH_FFMPEG` → **内置**（`imageio-ffmpeg`，随 venv 落盘）→ 系统 PATH。内置优先是刻意的：保证任何机器上解码行为一致，不被用户装的版本带偏。

容器类文件在解码前会先跑 `probe_with_ffmpeg()`（`ffmpeg -i` 只打头部、必然非零退出），几毫秒就能判断"没有音轨"或"超过 12 分钟"，避免把 40 分钟的片子整段转码后才拒绝。

### 分轨引擎：为什么不用 `demucs_onnx.separate()`

上游的 `separate()` 把分块循环封在函数内部，拿不到逐块回调——既不能中断，也做不出真进度。所以 `separator.py` 自己驱动 `InferenceSession`，并**照搬**它的分块算法（7.8 s 定长输入、1/4 重叠、三角窗 overlap-add）。代价是这段数值代码属于我们自己维护，收益是：

- 取消粒度 = 一个分块（实测 1–10 秒），而不是整整一个 stem
- `progress` 是真百分比，不是 0/25/50/75/100 的跳变

`_chunked_infer()` 与原实现逐采样一致（用真实权重比对过，max abs diff = 0.0）。**改动这段数值代码后要重新比对一次**：同一段音频分别跑 `demucs_onnx.separate(model="htdemucs_ft_vocals")` 与本函数，读两个 `vocals.wav` 逐采样求差，应小于 1e-3；否则是静默降质，业务测试发现不了。

`htdemucs_ft` 是"每个音源一个专家"的 bag，所以按 stem 逐个调用。

### 批式并发：为什么不是一个线程池跑四个专家

`separate_stems` 按 `parallel`（`PITCHMORPH_PARALLEL`，默认 1，上限 `config.MAX_PARALLEL`=4）把 stem 切批：**批内主线程预建 session → 并发跑 `_chunked_infer` → 全部 join → `session_pool().clear()` → 下一批**。三条约束决定了这个形状：

- `SessionPool.get()` 是**无锁**的 get-or-create，并发 miss 会 race，所以 session 只能由主线程预建。
- `SessionPool.clear()` 清的是**整个进程级池**，并行时调用会清掉别的 worker 正在用的 session，因此只能在 join 之后、批与批之间。
- 单 session 已经吃满物理核（线程扫描：默认 1.19 s 最优，24 线程反而 1.80 s），给一个 session 加 `intra_op_num_threads` 只会更慢，只能靠多个独立 session 重叠。

峰值内存**不是**"批大小 × 0.6 GB"：ORT 的 CPU arena 会随线程数膨胀。实测 `parallel=2` 处理 60 s 片段时 WorkingSet 就到约 10 GB，全曲跑到 10.2 GB 时进程被系统终止——所以默认并行度是 1，只有核数远多于 24 的机器才值得调大。`parallel` 会被 `min(parallel, len(tier.stems))` 夹住，所以单轨档位（vocals）恒为 1，前端对应控件置灰。

进度在并行下不能再用 `(base + done/chunks)/total`（会倒退），改为"各 stem 完成比例之和 / stem 数"，并在锁内取单调最大值后上报。取消用批内共享的 `threading.Event`：任一 worker 失败立即置位，其余 worker 在下一个分块边界退出。

### 任务层：内存表 + 单 worker

`jobs.py` 里没有数据库也没有队列服务：

- `Job` 持有 `stage / stem / frac / parallel / done / total / cancel(Event)`
- `JobRegistry` = dict + `ThreadPoolExecutor(max_workers=1)`
- 单 worker 是刻意的：并发跑四个 session 内存会冲到数 GB，还拖慢单曲
- `reap()` 每小时的 lifespan 任务清理超过 24h 的目录

进度有两个数字，别混用：`Job.frac`（0–1 的整体进度，下载和分块推理都往里写，`progress` 属性由它派生）和 `done/total`（已完成 stem 数，只用于文案）。`elapsed_sec` 只算处理时间，排队时长单独放 `wait_sec`。

### 前端：Vue 3 + TS + Vite，两个视图

源码在 `frontend/src/`，`npm run build` 把产物写进 `app/static/`（清空该目录，产物入库、勿手改）。

**视图切换必须用 `.is-active` 类**：样式里是 `.view { display: none }` + `.view.is-active { display: block }`。用 `v-show` 会白屏——它移除 inline `display` 后，类规则又把整块藏起来（2026-10-28 踩过）。

- **home**：添加任务卡片 + 任务列表（进行中 / 已完成 / 失败取消三组）
- **workbench**：四轨同步播放

列表状态来自 `GET /api/jobs`，不是前端内存，所以刷新不丢；轮询只在存在活跃任务时启动，任务完成后自己停。

添加任务卡片里的「并发」控件是**提交时参数**（1–4），缺省值来自 `/api/health` 的 `default_parallel`；单轨档位没有可并行的专家，控件置灰。任务列表只在 `parallel > 1` 时显示「并发 N」。

工作台用单个 `AudioContext` + 每轨一个 `MediaElement` + `GainNode`：播放时以第一轨为主时钟，`requestAnimationFrame` 里检测漂移（>80ms 就校正）。波形不调 `decodeAudioData`，而是直接解析 PCM16 WAV 字节算峰值——省掉一份 float32 的 AudioBuffer。已播放部分是叠在灰波形上的第二张 canvas，靠 `clip-path: inset(0 calc(100% - var(--p)) 0 0)` 裁切，避免每帧重绘。

### 磁盘布局

```
data/jobs/{job_id}/
├── input.wav        # 规整后的输入，retry 依赖它
├── stems/*.wav      # 文件名固定为 drums/bass/other/vocals
└── package.zip      # 下载全部，内部带一层以源文件命名的目录
data/models/         # HF 权重缓存（cache 布局，别手放文件进来）
```

`/media` 挂载在 `data/jobs` 上，所以 URL 是 `/media/{job_id}/stems/vocals.wav`——**不要写成 `/media/jobs/...`**，那样会 404（踩过一次）。

`stems/` 的下一层是留给 M2 的：音高线提取会新增 `data/jobs/{id}/features/`，与 `stems/` 平级，因此 `vocals.wav` 的路径必须保持稳定。

## 硬约束

- **改完并行或分块逻辑必须重跑**：`uv run python scripts/smoke_offline.py` 全过，且 parallel=1 与 parallel=2 跑同一首曲子的四轨输出逐采样比对 max abs diff < 1e-3（并发不应改变数值）。
- **不要安装 `demucs-onnx[export]`**：会拉 `torch>=2.4,<2.5` + `demucs==4.0.1`，与"无 PyTorch"目标直接冲突。输出 WAV 也不需要 `[mp3]`。
- **Python 锁定 3.11**（`requires-python = ">=3.11,<3.12"`）：`demucs-onnx` 只测到 3.13，3.14 未验证；`.python-version` 固定 3.11。
- **`.agent/` 不能被 gitignore**：`.agent/memory/` 是本项目的记忆目录（索引 `MEMORY.md`），要跟着仓库走。
- **`--reload` 会清空任务表**：任务状态在内存里，任何 `.py` 改动都会重启进程、丢掉进行中的任务。跑长任务前先去掉。
- 执行器由 `app/runtime.py` 决定，顺序是：显式 `PITCHMORPH_PROVIDERS` → CUDA 真探针 → CPU 兜底。**不要用 `get_available_providers()` 判断 GPU 可用**——装了 GPU wheel 但没有 CUDA DLL 的机器也会列出 `CUDAExecutionProvider`，失败会推迟到建 session 时才炸。真探针是 `preload_dlls()` + 一张 92 字节的 Identity 图建 session 并试跑一次。
- GPU 版与 CPU 版 `onnxruntime` 同模块名、不能共存。`pyproject.toml` 用 `[tool.uv] override-dependencies` 把 `demucs-onnx` 那条 `Requires-Dist: onnxruntime` 改写成 `sys_platform == 'darwin'`，于是 Windows/Linux 上 `uv sync` 只装 `onnxruntime-gpu`，**不需要 `--no-install-package`，也不需要 `--no-sync` 启动**。不要退回 extra 方案：`uv run` 每次启动都会 sync，会把 GPU 包换回 CPU 包，命令行就得出特例。
- `onnxruntime-gpu` 是**常规依赖**而不是 extra，所以没有 GPU 的机器也会装它——它自带 CPU EP，探针失败会回落，功能等价于 CPU 版。这是为「启动命令统一」付出的磁盘代价（约 160 MB，Windows 再加 cuDNN 400 MB）。
- **Windows 的 CUDA 运行库不能靠 pip**：`nvidia-*` 只发 manylinux wheel，`nvidia-cublas` 等没有 win_amd64，所以 `cudart` / `cublas` / `cufft` / `curand` / `nvrtc` 必须来自系统 CUDA Toolkit（13.x 均可，minor 版本兼容）。**这五个在安装器里是分开的组件——只装 Runtime 会缺 CuBLAS/CuFFT/CuRAND/NVRTC**，ORT 起不来。`nvidia-cudnn-cu13` 是唯一有 win_amd64 wheel 的，由 `uv sync` 装、`preload_dlls(cudnn=True)` 加载。
- **CUDA 13 的 DLL 在 `bin\x64`**，不是 `bin`。`app/runtime.py` 用 `os.add_dll_directory()` 注册（Python 3.8+ 已不从 PATH 找 DLL），所以无需改系统 PATH。PhysX 自带的 `cudart64_65.dll`（CUDA 6.5）是干扰项，必须按版本匹配 `cudart64_13.dll`。
- **探针要单独查 cuDNN**：最小 Identity 图不经过卷积，cuDNN 缺失时照样跑通，会误报「CUDA 可用」，然后真实模型在第一层 Conv 崩掉。`runtime._missing_cudnn()` 就是为此加的。
- 前端在 `frontend/`（Vue 3 + TS + Vite），`npm run build` 输出到 `app/static` 并清空该目录。改完前端必须重新构建，把 `app/static` 的产物一起提交；`frontend/node_modules/` 已忽略。`/api` 与 `/media` 在 `npm run dev` 下代理到 8000。

## 当前范围

只做分离。音高线提取（M2）与 OpenUtau 导入（M3）尚未实现，只在目录结构上留了接缝。看到任何 audio→MIDI 相关的代码或依赖都属于误加。

性能诊断（分轨为什么慢、GPU 可行性）的实测数据与待办见 [docs/perf-audit.md](docs/perf-audit.md)。
