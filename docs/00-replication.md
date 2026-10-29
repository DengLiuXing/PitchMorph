# 复刻指南：从零到一首歌分轨

> 这份文档假设你要**重建一个同类工具**，而不是给现有仓库打补丁。
> 顺序是「先能出声 → 再能选 → 再能打断 → 最后上 GPU」，每一步都有可验证的产出。
> 已经踩过的坑集中在本文件第 6 节，不用重新挖。

---

## 1. 先明确你在做什么

一句话：**本地把整首混音切成 4 条音轨，浏览器里同步试听**。

三个不可退让的约束，决定了后面所有选型：

| 约束 | 原因 |
|---|---|
| 推理不进 Web 进程的请求线程 | 一首歌几分钟，HTTP 请求撑不住 |
| 音频不出本机 | 用户拖进来的是自己的素材 |
| 单机、无 Redis / Celery | 目标用户是「自己电脑上用」，不是 SaaS |

如果你接受「上传到服务器排队」，可以直接用 Replicate / 云函数，本文档大半不适用。

---

## 2. 技术选型的真实代价

### 选 ONNX 而不是 PyTorch

原生 `demucs` 依赖 PyTorch，装完是 2.5 GB 起。当时的目标是「一个 `uv sync` 就能跑、不碰 CUDA Toolkit 之外的任何东西」，所以走 `demucs-onnx`：纯 ONNX Runtime + numpy，包体 160 MB，CPU/GPU 同一套代码。

**代价必须提前知道**：

1. **上游 API 不够用**。`demucs_onnx.separate()` 把整首歌的分块循环封在函数内部，没有逐块回调。想要「取消」和「真实进度」，只能自己驱动 `InferenceSession`，并逐行照搬它的分块算法（`7.8 s` 定长、1/4 重叠、三角窗 overlap-add）。这段数值代码从此归你维护。
2. **模型是别人导出的**。输入形状在导出时锁死为 `(1, 2, 343980)`，你无法改块长、无法改 batch。
3. **显存行为由 ORT 决定**，默认值面向长驻服务，不适合 8 GB 笔记本（详见 `03`）。

如果你更看重「模型可换、可微调、可导出 MIDI」，直接上 PyTorch，见 `03-pytorch-dual.md`。**不要**在 ONNX 这套上硬加新能力。

### 选「服务端推理 + 浏览器播放」

- 浏览器端跑 ONNX Runtime Web 也能做，但同一时刻只能用一个后端，四轨串行反而更慢，且模型要重新下载。
- 服务端的好处：写盘即缓存，下载和播放共用同一批 WAV，`/media` 静态托管天然支持 Range 请求（播放器拖动依赖它）。

---

## 3. 最小可运行版本（30 分钟）

目标：**命令行把一首歌分成 4 个 wav**。先不要 Web，不要 GPU。

```powershell
# Python 3.11 是硬要求：demucs-onnx 的依赖在 3.12+ 未验证
uv python pin 3.11
uv init --no-workspace
uv add demucs-onnx==0.3.4 onnxruntime soundfile soxr
```

```python
# try.py
from demucs_onnx import separate

out = separate(
    "song.mp3",
    "out",
    model="htdemucs_ft",          # 四专家集成
    precision="fp16weights",       # 权重减半，运行时精度不变
)
print({name: audio.shape for name, audio in out.items()})
```

**验证**：`out/` 下有 `drums.wav / bass.wav / other.wav / vocals.wav`，时长与源文件一致。

三个容易踩的点：

- `fp16weights` 只是**存储**用 fp16，图里仍然算 fp32，所以输出精度和 fp32 版几乎一样（max abs diff ~6e-5）。
- 首次运行会从 HuggingFace 下载 660 MB。国内先设 `$env:HF_ENDPOINT="https://hf-mirror.com"`，卡住再加 `$env:HF_HUB_DISABLE_XET="1"`。
- `demucs-onnx` 会硬拉 CPU 版 `onnxruntime`。如果你后面要 GPU，两者**同模块名、不能共存**，见第 5 节。

---

## 4. 加上 Web 层

### 4.1 为什么任务要落盘

```text
POST /api/jobs  →  202 + job_id（立刻返回）
GET  /api/jobs  ←  前端 900 ms 轮询
```

任务状态放内存（dict），但**产物必须落盘**：`data/jobs/{id}/stems/*.wav`。原因有三：

- `/media` 静态托管直接指向 `data/jobs`，播放和下载拿到的是同一份文件；
- 进程重启后任务表清空，但文件还在（配合 TTL 清理）；
- 「重试」不用重新上传——把已经规整好的 `input.wav` 复制到新任务目录即可。

### 4.2 解码：两条路，永不交叉

```python
try:
    data, rate = soundfile.read(src, dtype="float32", always_2d=True)
except Exception:
    ffmpeg -i src -map 0:a:0 -ac 2 -ar 44100 -c:a pcm_s16le out.wav
```

- `soundfile` 覆盖 wav / flac / mp3 / ogg / aiff，纯内存、无子进程。
- 容器（mp4 / m4s / mkv / ts）走 ffmpeg，**必须写 `-map 0:a:0`**，只取第一条音轨、丢掉画面。这一条让所有视频格式走同一条代码路径。
- ffmpeg 用 `imageio-ffmpeg` 的内置二进制，而不是系统 PATH 里的。跨机器行为一致比「用最新版」重要。
- 容器在转码前先 `ffmpeg -i` 读一次头部（必然非零退出），几毫秒就能判断「没有音轨」或「超过 12 分钟」，避免白转 40 分钟的视频。

### 4.3 单 worker 是特性，不是偷懒

```python
ThreadPoolExecutor(max_workers=1)
```

并发跑两个任务会让显存和内存同时翻倍，而单曲已经吃满设备。队列满了直接 429，比「慢慢排队但能接受」的更诚实。

---

## 5. 加上 GPU（Windows 最容易翻车的一步）

### 5.1 包冲突

`demucs-onnx` 声明依赖 `onnxruntime`（CPU 版），与 `onnxruntime-gpu` 同模块名、同名目录、文件重叠。**装了两个会随机坏**。

解法（`pyproject.toml`）：

```toml
[tool.uv]
override-dependencies = [
    "onnxruntime; sys_platform == 'darwin'",
]
```

把那条依赖改写成「只在 macOS 生效」，于是 Windows/Linux 上只装 GPU 版。这比 `--no-install-package` 好，因为 `uv run` 每次启动都会 sync，命令行特例迟早会被忘掉。

### 5.2 Windows 的 CUDA 运行库：一个容易误判的问题

先说结论：**「pip 能装 CUDA」对 PyTorch 成立，对 ONNX Runtime 不成立**。原因不在 pip，而在 wheel 里装了什么。

| | `torch==2.x+cu126` | `onnxruntime-gpu` |
|---|---|---|
| wheel 大小 | 约 2.3 GB | 约 160 MB |
| 自带 `cudart64_*.dll` | 是（在 `torch/lib/`） | 否 |
| 自带 `cublas` / `cudnn` / `cufft` | 是 | 否 |
| 装完能否直接跑 | 能 | 需系统里已有 CUDA |

PyTorch 把整个 CUDA 运行时打进了 wheel，所以 `pip install torch` 等价于装好了 CUDA。ORT 的 wheel 只含自己的 DLL，靠运行时 `dlopen` 系统库。**两者不可类比**——看到「pip 装 PyTorch 的 CUDA 就行」，不能推出「ORT 也能这么干」。

那 ORT 这边 pip 能给多少？按本项目的实际配置：

| 来源 | 提供 | 版本 |
|---|---|---|
| pip `nvidia-cublas` | cublas | 13.8.0.4（更晚的版本只发 manylinux） |
| pip `nvidia-cuda-nvrtc` | nvrtc | 13.4.92 |
| pip `nvidia-cudnn-cu13` | cudnn 全套 | 9.27，有 win_amd64 |
| pip `nvidia-cuda-runtime` / `cufft` / `curand` | **当时没装上** | 见下 |
| 系统 CUDA Toolkit | 全部五个 | 最终采用 |

装不全的三个原因，按实际踩到的顺序：

1. **包名变了**。CUDA 12 时代叫 `nvidia-cuda-runtime-cu12`，CUDA 13 去掉 `-cu13` 后缀，直接叫 `nvidia-cuda-runtime`。按旧名解析会落空。
2. **部分版本漏发 `win_amd64`**。不 pin 版本时 `uv` 会解析到 manylinux 构建，整个 resolve 在 Windows 上失败——所以 `pyproject.toml` 里才有 `nvidia-cublas==13.8.0.4`、`nvidia-cuda-nvrtc==13.4.92` 这种精确 pin。
3. **加载路径**。即使装上了，DLL 在 `site-packages/nvidia/*/bin`，不在 PATH 上；ORT 不会自己去找，必须 `os.add_dll_directory()`。

所以当时退回系统 Toolkit，因为它**一次覆盖全部五个**：

> Runtime + CuBLAS + CuFFT + CuRAND + NVRTC

只勾 Runtime 会缺四个，ORT 建 session 时报错，且错误只给 DLL 名称，很不直观。

**如果要重走纯 pip 路线**，三件事必须同时成立：装齐 `nvidia-cuda-runtime` / `nvidia-cufft` / `nvidia-curand`（注意无 `-cu13` 后缀）、pin 到仍发 `win_amd64` 的版本、在 `_register_windows_dll_dirs()` 里补注册 `site-packages/nvidia/*/bin`。这条路本项目没验证过，别当作已知可行。

CUDA 13 的 DLL 在 `bin\x64` 而不是 `bin`；Python 3.8+ 不再从 PATH 找 DLL，所以要在 `os.add_dll_directory()` 里显式注册。

### 5.3 不要用 `get_available_providers()` 判断

```python
# 错的
if "CUDAExecutionProvider" in ort.get_available_providers():
    use_gpu()
```

装了 GPU wheel 但缺 DLL 的机器，这个列表里**照样有** CUDA。真正的验证是：`preload_dlls()` + 建一个 92 字节的 Identity 图 + 跑一次。能建能跑才算可用。

### 5.4 验证清单

```powershell
uv run python scripts/setup.py       # 系统层：驱动 / Toolkit / cuDNN
uv run python -c "from app import runtime; print(runtime.providers())"
```

输出第一项是 `CUDAExecutionProvider`，且日志里没有回落原因，才算成功。

---

## 6. 六个必须知道的坑

| 坑 | 现象 | 正确做法 |
|---|---|---|
| 显存只增不减 | 单个专家跑两块从 3.2 GB 涨到 5.7 GB | 见 `03`：`enable_mem_pattern=False` + `kSameAsRequested` |
| 会话常驻 | 四个专家 = 7.8 GB，8 GB 卡 OOM | 每批跑完清空；重建约 4 s，比 OOM 便宜 |
| 清缓存的顺序 | `clear()` 后显存不还 | 先 `sessions.clear()` 断局部引用，再清全局缓存 |
| `v-show` 白屏 | 切视图后整块空白 | 用 `.view{display:none}` + `.view.is-active{display:block}` |
| `/media/jobs/...` 404 | 播放器 404 | `/media` 已经挂在 `data/jobs` 上，正确路径是 `/media/{id}/stems/x.wav` |
| `--reload` 丢任务 | 改了 `.py` 任务消失 | 任务表在内存，跑长任务前去掉 `--reload` |

---

## 7. 验收：怎样算复刻成功

按顺序全过一遍，任何一条不过都别往下走：

1. `uv run python scripts/smoke_offline.py` 全过（不需要权重，stub 掉 ONNX）
2. `uv run python scripts/smoke_media.py` 全过（mp4 / m4s / 无音轨 / 超长）
3. 命令行分轨一首 12 s 素材，四轨产出齐全
4. `uv run python scripts/bench.py samples/物凄.m4s --tier vocals`：CUDA 单块 < 1 s
5. 浏览器上传同一首，四轨能同步播放、拖动、静音、独奏
6. `nvidia-smi` 观察峰值 < 4 GB（8 GB 卡）
7. 取消一个进行中的任务，`stems/` 里没有半成品

---

## 8. 下一步

- 想改造成 PyTorch 生态、加音高分析、导出 MIDI → 读 `03-pytorch-dual.md`
- 想知道每个性能数字怎么来的、哪些方向已经试过并否决 → 读 `perf-audit.md`
- 想复刻前端那套交互 → 读 `02-frontend.md`
