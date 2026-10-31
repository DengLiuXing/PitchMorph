"""应用装配：日志、EP 自检、路由、静态站点挂载。"""
from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import runtime
from .api import router
from .config import DATA_DIR, WEB_DIST, setup_logging

setup_logging()
logger = logging.getLogger("pitchmorph.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    info = runtime.status()
    logger.info("启动完成 ep=%s ort=%s", info["ep"], info["version"])
    if info["fallback_reason"]:
        logger.warning(info["fallback_reason"])
    yield


app = FastAPI(
    title="PitchMorph V3",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
DATA_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")

    @app.get("/{path:path}")
    def spa_fallback(path: str):
        target = WEB_DIST / path
        if target.is_file():
            return FileResponse(target)
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(WEB_DIST / "index.html")
else:

    @app.get("/")
    def placeholder() -> dict:
        return {"message": "前端尚未构建：在 web/ 下执行 npm run build，或用 npm run dev 开发。"}
