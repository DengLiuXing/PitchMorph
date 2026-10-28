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
node --check app/static/app.js           # 前端只有语法检查，没有打包步骤
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
POST /api/jobs (multipart: file + tier)
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

`htdemucs_ft` 是"每个音源一个专家"的 bag，所以按 stem 循环调用，每个 stem 之间 `session_pool().clear()`——池是进程级的，不清就会同时驻留四个 session（约 2.4 GB）。

### 任务层：内存表 + 单 worker

`jobs.py` 里没有数据库也没有队列服务：

- `Job` 持有 `stage / stem / frac / done / total / cancel(Event)`
- `JobRegistry` = dict + `ThreadPoolExecutor(max_workers=1)`
- 单 worker 是刻意的：并发跑四个 session 内存会冲到数 GB，还拖慢单曲
- `reap()` 每小时的 lifespan 任务清理超过 24h 的目录

进度有两个数字，别混用：`Job.frac`（0–1 的整体进度，下载和分块推理都往里写，`progress` 属性由它派生）和 `done/total`（已完成 stem 数，只用于文案）。`elapsed_sec` 只算处理时间，排队时长单独放 `wait_sec`。

### 前端：免构建、两个视图

`app/static/` 下三个文件，没有 npm、没有 CDN、没有打包：

- **home**：添加任务卡片 + 任务列表（进行中 / 已完成 / 失败取消三组）
- **workbench**：四轨同步播放

列表状态来自 `GET /api/jobs`，不是前端内存，所以刷新不丢；轮询只在存在活跃任务时启动，任务完成后自己停。

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

- **不要安装 `demucs-onnx[export]`**：会拉 `torch>=2.4,<2.5` + `demucs==4.0.1`，与"无 PyTorch"目标直接冲突。输出 WAV 也不需要 `[mp3]`。
- **Python 锁定 3.11**（`requires-python = ">=3.11,<3.12"`）：`demucs-onnx` 只测到 3.13，3.14 未验证；`.python-version` 固定 3.11。
- **`.agent/` 不能被 gitignore**：`.agent/memory/` 是本项目的记忆目录（索引 `MEMORY.md`），要跟着仓库走。
- **`--reload` 会清空任务表**：任务状态在内存里，任何 `.py` 改动都会重启进程、丢掉进行中的任务。跑长任务前先去掉。
- ONNX 执行器默认 `CPUExecutionProvider`，不做自动探测（走错会以晦涩的 ORT 错误终止）；需要 GPU 时用 `PITCHMORPH_PROVIDERS` 显式开启。

## 当前范围

只做分离。音高线提取（M2）与 OpenUtau 导入（M3）尚未实现，只在目录结构上留了接缝。看到任何 audio→MIDI 相关的代码或依赖都属于误加。

性能诊断（分轨为什么慢、GPU 可行性）的实测数据与待办见 [docs/perf-audit.md](docs/perf-audit.md)。
