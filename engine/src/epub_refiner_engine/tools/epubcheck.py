"""EPUBCheck (Java) 电子书行业规范质检封装（支持缺失 Java 时友好降级）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from epub_refiner_engine.models import EpubCheckMessage, EpubCheckResult


def _find_java() -> str | None:
    java_home = os.environ.get("JAVA_HOME", "").strip()
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("java")
    if found:
        return found
    fallback = Path(r"E:\java\JDK\bin\java.exe")
    if fallback.exists():
        return str(fallback)
    return None


def _find_epubcheck_jar() -> Path | None:
    env_jar = os.environ.get("EPUBCHECK_JAR", "").strip()
    if env_jar:
        p = Path(env_jar)
        if p.exists():
            return p

    # 检查常见相对路径或工作区工具目录
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        candidate = parent / "tools" / "epubcheck" / "epubcheck-5.4.0" / "epubcheck.jar"
        if candidate.exists():
            return candidate
    return None


def is_epubcheck_available() -> tuple[bool, str]:
    """检测本机是否具备 Java + EPUBCheck 专业质检条件。"""
    if not _find_java():
        return False, "未检测到 Java 运行环境，将跳过专业规范质检，但仍可正常完成格式转换与基础精排。"
    if not _find_epubcheck_jar():
        return False, "未找到 EPUBCheck 质检组件，将跳过专业规范质检，但仍可正常完成基础精排。"
    return True, "已就绪"


def run_epubcheck(epub_path: str | Path, timeout_sec: int = 90) -> EpubCheckResult:
    """运行 EPUBCheck 执行电子书行业规范质检；环境缺失时优雅跳过。"""
    target = Path(epub_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"待质检的 EPUB 文件不存在: {target}")

    java_exe = _find_java()
    jar_path = _find_epubcheck_jar()
    if not java_exe or not jar_path:
        _, reason = is_epubcheck_available()
        return EpubCheckResult(
            passed=True,
            skipped=True,
            skip_reason=reason,
        )

    with tempfile.TemporaryDirectory(prefix="epubcheck_") as tmp:
        report_json = Path(tmp) / "report.json"
        cmd = [
            java_exe,
            "-Dfile.encoding=UTF-8",
            "-jar",
            str(jar_path),
            str(target),
            "--json",
            str(report_json),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )

        if not report_json.exists():
            return EpubCheckResult(
                passed=False,
                fatal_count=1,
                messages=[
                    EpubCheckMessage(
                        id="RUNNER_ERR",
                        severity="FATAL",
                        message="专业质检未能生成报告，文件可能严重损坏。",
                    )
                ],
                raw_stderr=(proc.stderr or proc.stdout or "")[:2000],
            )

        data = json.loads(report_json.read_text(encoding="utf-8"))
        checker = data.get("checker", {})
        fatal_count = int(checker.get("nFatal", 0))
        error_count = int(checker.get("nError", 0))
        warning_count = int(checker.get("nWarning", 0))

        messages: list[EpubCheckMessage] = []
        for item in data.get("messages", []):
            severity = str(item.get("severity", ""))
            if severity not in {"FATAL", "ERROR", "WARNING"}:
                continue
            locs = item.get("locations") or [{}]
            loc = locs[0] if locs else {}
            messages.append(
                EpubCheckMessage(
                    id=str(item.get("ID", "")),
                    severity=severity,
                    message=str(item.get("message", "")),
                    file_path=str(loc.get("path", "")),
                    line=loc.get("line"),
                    column=loc.get("column"),
                )
            )

        passed = (fatal_count == 0) and (error_count == 0)
        return EpubCheckResult(
            passed=passed,
            fatal_count=fatal_count,
            error_count=error_count,
            warning_count=warning_count,
            messages=messages,
            raw_stderr=(proc.stderr or "")[:1000],
        )
