"""Core Agent 数据模型与状态机定义。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskState(StrEnum):
    CREATED = "created"
    DIAGNOSING = "diagnosing"
    AWAITING_CONFIRM = "awaiting_confirm"
    PROCESSING = "processing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    step: str
    message: str
    level: Literal["info", "success", "warning", "error"] = "info"
    detail: dict[str, Any] | None = None


class EpubCheckMessage(BaseModel):
    id: str = ""
    severity: str = ""  # FATAL, ERROR, WARNING, INFO
    message: str = ""
    file_path: str = ""
    line: int | None = None
    column: int | None = None


class EpubCheckResult(BaseModel):
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    fatal_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    messages: list[EpubCheckMessage] = Field(default_factory=list)
    raw_stderr: str = ""


class ChapterPreview(BaseModel):
    index: int
    volume: str | None = None
    title: str
    char_count: int
    excerpt: str


class TxtRules(BaseModel):
    title: str
    author: str = "佚名"
    language: str = "zh-CN"
    volume_regex: str = ""
    chapter_regex: str
    ad_regexes: list[str] = Field(default_factory=list)
    style_preset: Literal["classic", "modern", "comfort"] = "classic"


class TxtDiagnosisReport(BaseModel):
    source_path: str
    file_size_bytes: int
    detected_encoding: str
    mojibake_repaired: bool = False
    sample_text_preview: str = ""
    total_lines: int
    total_chars: int
    used_llm: bool = False
    llm_note: str = ""
    rules: TxtRules
    chapter_count: int
    warnings: list[str] = Field(default_factory=list)
    previews: list[ChapterPreview] = Field(default_factory=list)


class EpubIssue(BaseModel):
    code: str
    label: str
    severity: Literal["error", "warning", "info"]
    auto_fixable: bool = True
    description: str


class EpubDiagnosisReport(BaseModel):
    source_path: str
    file_size_bytes: int
    has_drm: bool = False
    title: str = ""
    author: str = ""
    language: str = "zh-CN"
    has_cover: bool = False
    spine_count: int = 0
    toc_count: int = 0
    initial_check: EpubCheckResult
    issues: list[EpubIssue] = Field(default_factory=list)


class TaskRecord(BaseModel):
    task_id: str
    kind: Literal["txt", "epub"]
    state: TaskState = TaskState.CREATED
    source_path: str
    output_path: str | None = None
    utf8_txt_output_path: str | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    txt_report: TxtDiagnosisReport | None = None
    epub_report: EpubDiagnosisReport | None = None
    final_check: EpubCheckResult | None = None
    error: str | None = None
