# PitchMorph V3 启动脚本：按需构建 web 前端 + 启动后端
# 用法: .\start.ps1  （在仓库根目录执行）
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ---- 前端：源码比 dist 新（或 dist 不存在）才构建 ----
$distIndex = Join-Path $PSScriptRoot 'web\dist\index.html'
$needBuild = -not (Test-Path $distIndex)
if (-not $needBuild) {
    $builtAt = (Get-Item $distIndex).LastWriteTimeUtc
    $newest = Get-ChildItem -Path (Join-Path $PSScriptRoot 'web\src'),
        (Join-Path $PSScriptRoot 'web\package.json'),
        (Join-Path $PSScriptRoot 'web\package-lock.json') -Recurse -File |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $needBuild = $null -ne $newest -and $newest.LastWriteTimeUtc -gt $builtAt
}

Push-Location web
if ($needBuild) {
    if (-not (Test-Path node_modules)) {
        Write-Host '[1/3] 安装前端依赖...' -ForegroundColor Cyan
        npm install
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'npm install 失败' }
    }
    Write-Host '[2/3] 构建前端 (vue-tsc + vite)...' -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw '前端构建失败' }
} else {
    Write-Host '[1/3] 前端已是最新构建，跳过' -ForegroundColor DarkGray
    Write-Host '[2/3] 跳过构建' -ForegroundColor DarkGray
}
Pop-Location

# ---- 后端：.venv 里的 uvicorn，起在 127.0.0.1:8000 ----
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "找不到虚拟环境解释器：$python" }

Write-Host '[3/3] 启动后端，就绪后访问 http://127.0.0.1:8000' -ForegroundColor Cyan
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
