"""本地 HTTP 服务：桌面界面通过它与 AI Agent 引擎通信。"""

from __future__ import annotations

import os
import secrets
import sys
import threading
import time

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from epub_refiner_engine.agent.llm_client import fetch_available_models
from epub_refiner_engine.agent.orchestrator import TaskOrchestrator
from epub_refiner_engine.config import SUPPORTED_EXTENSIONS, settings
from epub_refiner_engine.models import TaskRecord, TaskState, TxtRules
from epub_refiner_engine.secrets_store import (
    get_llm_settings,
    get_output_dir_settings,
    mask_api_key,
    reset_llm_settings,
    save_llm_settings,
    save_output_dir,
)
from epub_refiner_engine.tools.epubcheck import is_epubcheck_available

ENGINE_NAME = "epub-refiner-engine"
ENGINE_VERSION = "0.2.0"

app = FastAPI(title="EPUB Refiner Engine", version=ENGINE_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

orchestrator = TaskOrchestrator()


def require_token(x_session_token: str | None = Header(default=None)) -> None:
    """校验会话令牌，防止本机其他程序冒充界面调用引擎。"""
    expected = settings.session_token
    if not expected:
        return
    if not x_session_token or not secrets.compare_digest(x_session_token, expected):
        raise HTTPException(status_code=401, detail="invalid session token")


class ApiKeyPayload(BaseModel):
    api_key: str = ""
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str = ""


class FetchModelsPayload(BaseModel):
    provider: str = "deepseek"
    base_url: str = ""
    api_key: str = ""


class OutputDirPayload(BaseModel):
    output_dir: str = ""


class DiagnoseRequest(BaseModel):
    file_path: str
    mode: str = "auto"


class ConfirmEpubRequest(BaseModel):
    title: str
    author: str = "佚名"
    language: str = "zh-CN"
    fix_cover: bool = True
    sanitize_scripts: bool = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info", dependencies=[Depends(require_token)])
def info() -> dict[str, object]:
    ec_ok, ec_note = is_epubcheck_available()
    llm_cfg = get_llm_settings()
    out_cfg = get_output_dir_settings()
    return {
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "python": sys.version.split()[0],
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "epubcheck_configured": ec_ok,
        "epubcheck_note": ec_note,
        "deepseek_configured": llm_cfg.is_ready,
        "llm_provider": llm_cfg.provider,
        "llm_model": llm_cfg.model,
        "output_dir": out_cfg["output_dir"],
        "output_dir_is_custom": out_cfg["is_custom"],
        "default_output_dir": out_cfg["default_dir"],
    }


def _llm_status_dict() -> dict[str, object]:
    cfg = get_llm_settings()
    return {
        "configured": cfg.is_ready,
        "masked": mask_api_key(cfg.api_key),
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
    }


@app.get("/api/settings/api-key/status", dependencies=[Depends(require_token)])
def api_key_status() -> dict[str, object]:
    return _llm_status_dict()


@app.post("/api/settings/api-key", dependencies=[Depends(require_token)])
def save_api_key(payload: ApiKeyPayload) -> dict[str, object]:
    try:
        save_llm_settings(
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _llm_status_dict()


@app.delete("/api/settings/api-key", dependencies=[Depends(require_token)])
def remove_api_key() -> dict[str, object]:
    reset_llm_settings()
    return _llm_status_dict()


@app.post("/api/settings/models", dependencies=[Depends(require_token)])
def list_available_models(payload: FetchModelsPayload) -> dict[str, object]:
    cfg = get_llm_settings()
    effective_key = payload.api_key.strip() or (cfg.api_key or "")
    try:
        models = fetch_available_models(
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=effective_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": models, "count": len(models)}


@app.get("/api/settings/output-dir", dependencies=[Depends(require_token)])
def read_output_dir() -> dict[str, object]:
    return get_output_dir_settings()


@app.post("/api/settings/output-dir", dependencies=[Depends(require_token)])
def update_output_dir(payload: OutputDirPayload) -> dict[str, object]:
    try:
        return save_output_dir(payload.output_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tasks/diagnose", response_model=TaskRecord, dependencies=[Depends(require_token)])
def diagnose(req: DiagnoseRequest) -> TaskRecord:
    try:
        return orchestrator.diagnose_file(req.file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/tasks/start-diagnose",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def start_diagnose(req: DiagnoseRequest, background_tasks: BackgroundTasks) -> TaskRecord:
    try:
        task = orchestrator.create_diagnose_task(req.file_path, mode=req.mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(orchestrator.run_diagnose_task, task.task_id)
    return task


@app.post(
    "/api/tasks/{task_id}/start-confirm-txt",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def start_confirm_txt(
    task_id: str, rules: TxtRules, background_tasks: BackgroundTasks
) -> TaskRecord:
    task = orchestrator.get_task(task_id)
    if not task or task.kind != "txt":
        raise HTTPException(status_code=404, detail="未找到对应的 TXT 任务")
    task.state = TaskState.PROCESSING
    background_tasks.add_task(orchestrator.confirm_txt, task_id, rules)
    return task


@app.post(
    "/api/tasks/{task_id}/start-confirm-epub",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def start_confirm_epub(
    task_id: str, req: ConfirmEpubRequest, background_tasks: BackgroundTasks
) -> TaskRecord:
    task = orchestrator.get_task(task_id)
    if not task or task.kind != "epub":
        raise HTTPException(status_code=404, detail="未找到对应的 EPUB 任务")
    task.state = TaskState.PROCESSING
    background_tasks.add_task(
        orchestrator.confirm_epub,
        task_id,
        req.title,
        req.author,
        req.language,
        req.fix_cover,
        req.sanitize_scripts,
    )
    return task


class ExportUtf8Request(BaseModel):
    title: str | None = None


@app.post(
    "/api/tasks/{task_id}/export-utf8-txt",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def export_utf8_txt(task_id: str, req: ExportUtf8Request | None = None) -> TaskRecord:
    try:
        return orchestrator.export_utf8_txt(task_id, req.title if req else None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/tasks/{task_id}/confirm-txt",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def confirm_txt(task_id: str, rules: TxtRules) -> TaskRecord:
    try:
        return orchestrator.confirm_txt(task_id, rules)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/tasks/{task_id}/confirm-epub",
    response_model=TaskRecord,
    dependencies=[Depends(require_token)],
)
def confirm_epub(task_id: str, req: ConfirmEpubRequest) -> TaskRecord:
    try:
        return orchestrator.confirm_epub(
            task_id=task_id,
            title=req.title,
            author=req.author,
            language=req.language,
            fix_cover=req.fix_cover,
            sanitize_scripts=req.sanitize_scripts,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}", response_model=TaskRecord, dependencies=[Depends(require_token)])
def get_task(task_id: str) -> TaskRecord:
    task = orchestrator.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _watch_parent(pid: int) -> None:
    """外壳进程消失后自动退出，防止 Windows 上残留僵尸进程。"""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        os._exit(0)
    kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    time.sleep(0.2)
    os._exit(0)


def run() -> None:
    if settings.parent_pid and sys.platform == "win32":
        threading.Thread(target=_watch_parent, args=(settings.parent_pid,), daemon=True).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
