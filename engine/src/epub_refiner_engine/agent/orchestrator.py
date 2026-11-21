"""Core Agent 任务编排器：管理诊断、人机协同确认、构建与 EPUBCheck 自愈闭环。"""

from __future__ import annotations

import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from epub_refiner_engine.agent.llm_client import (
    infer_txt_rules,
    repair_file_with_deepseek,
)
from epub_refiner_engine.config import SUPPORTED_EXTENSIONS
from epub_refiner_engine.models import (
    AgentEvent,
    EpubCheckMessage,
    EpubCheckResult,
    TaskRecord,
    TaskState,
    TxtDiagnosisReport,
    TxtRules,
)
from epub_refiner_engine.secrets_store import get_effective_output_dir
from epub_refiner_engine.tools.epub_builder import build_epub3_from_chapters
from epub_refiner_engine.tools.epub_inspector import inspect_epub, repack_refined_epub
from epub_refiner_engine.tools.epubcheck import run_epubcheck
from epub_refiner_engine.tools.txt_tools import (
    build_previews,
    detect_corruption_warnings,
    export_clean_utf8_txt,
    extract_txt_sample,
    read_txt_with_encoding,
    split_txt_by_rules,
)


def _safe_output_path(source: Path, book_title: str, suffix: str = "_精排.epub") -> Path:
    clean_title = "".join(c for c in book_title if c not in r'\/:*?"<>|').strip() or source.stem
    ext = Path(suffix).suffix
    stem_part = suffix[: -len(ext)] if ext else suffix
    out_dir = get_effective_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = out_dir / f"{clean_title}{stem_part}{ext}"
    # 绝不覆盖原文件或已有文件
    idx = 1
    while candidate.exists() or candidate.resolve() == source.resolve():
        candidate = out_dir / f"{clean_title}{stem_part}({idx}){ext}"
        idx += 1
    return candidate


class TaskOrchestrator:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._task_modes: dict[str, str] = {}
        self._txt_cache: dict[str, str] = {}
        self._work_dirs: dict[str, Path] = {}

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def _emit(
        self,
        task: TaskRecord,
        step: str,
        message: str,
        level: Literal["info", "success", "warning", "error"] = "info",
    ) -> None:
        task.events.append(AgentEvent(step=step, message=message, level=level))

    def create_diagnose_task(self, file_path: str, mode: str = "auto") -> TaskRecord:
        src = Path(file_path).resolve()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"文件不存在: {src}")
        ext = src.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"仅支持 .txt 和 .epub 格式，当前文件为: {ext or '无扩展名'}")

        task_id = uuid.uuid4().hex[:12]
        kind: Literal["txt", "epub"] = "txt" if ext == ".txt" else "epub"
        task = TaskRecord(task_id=task_id, kind=kind, state=TaskState.DIAGNOSING, source_path=str(src))
        self._tasks[task_id] = task
        self._task_modes[task_id] = mode
        return task

    def run_diagnose_task(self, task_id: str) -> TaskRecord:
        task = self._tasks[task_id]
        src = Path(task.source_path)
        try:
            if task.kind == "txt":
                self._diagnose_txt(task, src)
            else:
                self._diagnose_epub(task, src)
        except Exception as exc:  # noqa: BLE001
            task.state = TaskState.FAILED
            task.error = str(exc)
            self._emit(task, "诊断失败", str(exc), "error")
        return task

    def diagnose_file(self, file_path: str) -> TaskRecord:
        task = self.create_diagnose_task(file_path)
        return self.run_diagnose_task(task.task_id)

    def _diagnose_txt(self, task: TaskRecord, src: Path) -> None:
        self._emit(task, "文字识别", f"正在读取 {src.name}，检查文字编码并排查乱码…")
        text, encoding = read_txt_with_encoding(src)
        self._txt_cache[task.task_id] = text
        mojibake_repaired = "错位乱码" in encoding
        self._emit(
            task,
            "文字识别",
            f"识别完成（原格式：{encoding}），已整理为清晰汉字文本，共 {len(text):,} 字。",
            "success",
        )

        corruption_warnings = detect_corruption_warnings(text)
        for cw in corruption_warnings:
            self._emit(task, "文字提醒", cw, "warning")

        mode = self._task_modes.get(task.task_id, "auto")
        if mode == "txt_only":
            clean_title = src.stem
            task.txt_report = TxtDiagnosisReport(
                source_path=str(src),
                file_size_bytes=src.stat().st_size,
                detected_encoding=encoding,
                mojibake_repaired=mojibake_repaired,
                sample_text_preview=text[:400],
                total_lines=len(text.splitlines()),
                total_chars=len(text),
                used_llm=False,
                llm_note="仅执行 TXT 乱码识别与 UTF-8 转码，不会转换为 EPUB。",
                rules=TxtRules(title=clean_title, chapter_regex=r"^第.{1,12}[章回节].*"),
                chapter_count=0,
                warnings=corruption_warnings,
                previews=[],
            )
            task.state = TaskState.AWAITING_CONFIRM
            self._emit(task, "等待导出", "编码识别完成！请预览汉字无误后，点击保存为纯净 UTF-8 TXT。")
            return

        self._emit(task, "抽取片段", "正在抽取开头、中段及重复出现的行用于识别章节（不会上传整本书）…")
        sample = extract_txt_sample(text, src.stem)

        self._emit(task, "识别规律", "正在识别书名、章节规律和需要清理的广告行…")
        rules, used_llm, note = infer_txt_rules(sample, src.stem)
        self._emit(task, "识别规律", note, "success" if used_llm else "warning")

        chapters, split_warnings = split_txt_by_rules(text, rules)
        all_warnings = [*corruption_warnings, *split_warnings]
        previews = build_previews(chapters)
        self._emit(
            task,
            "试排目录",
            f"试排完成：共识别出 {len(chapters)} 章。",
            "warning" if all_warnings else "success",
        )

        task.txt_report = TxtDiagnosisReport(
            source_path=str(src),
            file_size_bytes=src.stat().st_size,
            detected_encoding=encoding,
            mojibake_repaired=mojibake_repaired,
            sample_text_preview=text[:400],
            total_lines=len(text.splitlines()),
            total_chars=len(text),
            used_llm=used_llm,
            llm_note=note,
            rules=rules,
            chapter_count=len(chapters),
            warnings=all_warnings,
            previews=previews,
        )
        task.state = TaskState.AWAITING_CONFIRM
        self._emit(task, "等待确认", "检查完成！你可以预览效果后，另存为纯净文本或生成精排电子书。")

    def export_utf8_txt(self, task_id: str, book_title: str | None = None) -> TaskRecord:
        task = self._tasks.get(task_id)
        if not task or task.kind != "txt":
            raise KeyError("未找到对应的 TXT 任务")
        text = self._txt_cache.get(task_id)
        if text is None:
            raise RuntimeError("任务文本缓存已失效，请重新拖入文件。")

        title = book_title or (task.txt_report.rules.title if task.txt_report else Path(task.source_path).stem)
        out_txt = _safe_output_path(Path(task.source_path), title, suffix="_UTF8.txt")
        export_clean_utf8_txt(text, out_txt)
        task.utf8_txt_output_path = str(out_txt)
        self._emit(task, "导出 UTF-8", f"已另存为标准 UTF-8 中文小说：{out_txt}", "success")
        return task

    def _diagnose_epub(self, task: TaskRecord, src: Path) -> None:
        work_dir = Path(tempfile.mkdtemp(prefix=f"epub_work_{task.task_id}_"))
        self._work_dirs[task.task_id] = work_dir
        self._emit(task, "安全读取", f"正在安全读取 {src.name}（拦截异常压缩路径）…")

        report = inspect_epub(src, work_dir)
        task.epub_report = report

        if report.has_drm:
            task.state = TaskState.FAILED
            task.error = "这本书带有版权加密锁（DRM），本工具不破解加密书，已自动停止。"
            self._emit(task, "安全拦截", task.error, "error")
            return

        if report.initial_check.skipped:
            self._emit(
                task,
                "健康检查",
                f"基础体检完成：发现 {len(report.issues)} 项可优化问题。（{report.initial_check.skip_reason}）",
                "warning" if report.issues else "success",
            )
        else:
            err_cnt = report.initial_check.fatal_count + report.initial_check.error_count
            self._emit(
                task,
                "健康检查",
                f"体检完成：发现 {len(report.issues)} 项可优化内容（规范质检发现 {err_cnt} 处结构报错）。",
                "warning" if report.issues else "success",
            )
        task.state = TaskState.AWAITING_CONFIRM
        self._emit(task, "等待确认", "请确认书名、作者与要清理的项目，然后点击开始精排。")

    def confirm_txt(self, task_id: str, rules: TxtRules) -> TaskRecord:
        task = self._tasks.get(task_id)
        if not task or task.kind != "txt":
            raise KeyError("未找到对应的 TXT 任务")
        text = self._txt_cache.get(task_id)
        if text is None:
            raise RuntimeError("任务文本缓存已失效，请重新拖入文件。")

        task.state = TaskState.PROCESSING
        self._emit(task, "拆分章节", "正在按确认后的规律清理广告并拆分全书章节…")
        chapters, _ = split_txt_by_rules(text, rules)

        out_path = _safe_output_path(Path(task.source_path), rules.title)
        self._emit(task, "制作电子书", f"正在排版生成精排电子书：{out_path.name}（含目录与简洁封面）…")
        build_epub3_from_chapters(
            output_path=out_path,
            title=rules.title,
            author=rules.author,
            language=rules.language,
            chapters=chapters,
            style_preset=rules.style_preset,
        )

        task.state = TaskState.VERIFYING
        self._emit(task, "规范质检", "正在检查电子书是否符合通用阅读器规范…")
        check_res = run_epubcheck(out_path)
        task.final_check = check_res
        task.output_path = str(out_path)

        if check_res.skipped:
            task.state = TaskState.COMPLETED
            self._emit(task, "完成", f"电子书已生成：{out_path}（{check_res.skip_reason}）", "success")
        elif check_res.passed:
            task.state = TaskState.COMPLETED
            self._emit(task, "完成", f"质检 0 报错通过！已保存至 {out_path}", "success")
        else:
            task.state = TaskState.COMPLETED
            self._emit(
                task,
                "完成(含提醒)",
                f"文件已保存至 {out_path}，质检仍有 {check_res.error_count} 处待留意提示。",
                "warning",
            )
        return task

    def confirm_epub(
        self,
        task_id: str,
        title: str,
        author: str,
        language: str = "zh-CN",
        fix_cover: bool = True,
        sanitize_scripts: bool = True,
    ) -> TaskRecord:
        task = self._tasks.get(task_id)
        if not task or task.kind != "epub":
            raise KeyError("未找到对应的 EPUB 任务")
        work_dir = self._work_dirs.get(task_id)
        if not work_dir or not work_dir.exists():
            raise RuntimeError("任务工作目录不存在，请重新拖入文件。")

        task.state = TaskState.PROCESSING
        out_path = _safe_output_path(Path(task.source_path), title)
        self._emit(task, "整理精排", "正在整理书名作者信息、清理危险网页代码并补全目录与封面…")
        repack_refined_epub(
            work_dir=work_dir,
            output_path=out_path,
            title=title,
            author=author,
            language=language,
            fix_cover=fix_cover,
            sanitize_scripts=sanitize_scripts,
        )

        # 规范质检 + AI 自动修补闭环（最多 3 轮）
        check_res = self._verify_and_repair_loop(task, work_dir, out_path, max_rounds=3)
        task.final_check = check_res
        task.output_path = str(out_path)
        task.state = TaskState.COMPLETED
        if check_res.skipped:
            self._emit(task, "完成", f"电子书已整理保存至 {out_path}（{check_res.skip_reason}）", "success")
        elif check_res.passed:
            self._emit(task, "完成", f"质检 0 报错通过！已保存至 {out_path}", "success")
        else:
            self._emit(
                task,
                "完成(含残余报错)",
                f"已保存 {out_path}，仍余 {check_res.fatal_count + check_res.error_count} 处待留意问题。",
                "warning",
            )
        return task

    def _verify_and_repair_loop(
        self,
        task: TaskRecord,
        work_dir: Path,
        out_path: Path,
        max_rounds: int = 3,
    ) -> EpubCheckResult:
        for round_idx in range(1, max_rounds + 1):
            task.state = TaskState.VERIFYING
            self._emit(task, "规范质检", f"正在进行第 {round_idx} 轮阅读器规范质检…")
            res = run_epubcheck(out_path)
            if res.skipped or res.passed:
                return res

            err_total = res.fatal_count + res.error_count
            self._emit(task, "发现问题", f"检测到 {err_total} 处结构报错，尝试调用 AI 自动修补…", "warning")
            task.state = TaskState.REPAIRING

            by_file: dict[str, list[EpubCheckMessage]] = {}
            for msg in res.messages:
                if msg.severity in {"FATAL", "ERROR"} and msg.file_path:
                    by_file.setdefault(msg.file_path, []).append(msg)

            repaired_any = False
            for rel_path, errs in by_file.items():
                target = (work_dir / rel_path).resolve()
                if not target.is_relative_to(work_dir.resolve()) or not target.exists():
                    continue
                fixed_text, note = repair_file_with_deepseek(
                    rel_path, target.read_text(encoding="utf-8", errors="replace"), errs
                )
                if fixed_text:
                    target.write_text(fixed_text, encoding="utf-8")
                    repaired_any = True
                    self._emit(task, "自动修复", f"{rel_path}: {note}", "info")
                else:
                    self._emit(task, "跳过修复", f"{rel_path}: {note}", "warning")

            if not repaired_any:
                return res

            # 重新打包
            tmp_out = out_path.with_suffix(".epub.tmp")
            with zipfile.ZipFile(tmp_out, "w") as zf:
                zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
                for fp in sorted(work_dir.rglob("*")):
                    if fp.is_dir():
                        continue
                    rel = fp.relative_to(work_dir).as_posix()
                    if rel == "mimetype":
                        continue
                    zf.write(fp, arcname=rel, compress_type=zipfile.ZIP_DEFLATED)
            tmp_out.replace(out_path)

        return run_epubcheck(out_path)

    def cleanup_task(self, task_id: str) -> None:
        self._txt_cache.pop(task_id, None)
        wd = self._work_dirs.pop(task_id, None)
        if wd and wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
