import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { ApiKeyModal } from "./components/ApiKeyModal";
import { TimelinePanel } from "./components/TimelinePanel";
import type {
  BatchItem,
  EngineConfig,
  EngineInfo,
  LlmStatus,
  OutputDirSettings,
  StylePreset,
  TaskRecord,
  TxtRules,
  WorkMode,
} from "./types";
import "./App.css";

const CLOSE_PREF_KEY = "epub_refiner_close_action";

const MODE_META: Record<
  WorkMode,
  { title: string; short: string; desc: string; acceptExt: ".txt" | ".epub"; filterExt: string[] }
> = {
  txt_only: {
    title: "① 仅修复乱码 TXT → 正常 TXT",
    short: "只转码为纯净 UTF-8 小说，绝不生成 EPUB",
    desc: "适合只想把乱码、GBK 等旧格式 TXT 还原成正常简体中文 TXT 的情况。",
    acceptExt: ".txt",
    filterExt: ["txt"],
  },
  txt_to_epub: {
    title: "② TXT 一步精排为 EPUB",
    short: "自动转码、分章、去广告并生成精排电子书",
    desc: "适合把正常的（或乱码的）TXT 小说直接排版成带目录与封面的精排 EPUB。",
    acceptExt: ".txt",
    filterExt: ["txt"],
  },
  epub_refine: {
    title: "③ 残缺 EPUB → 精排修复 EPUB",
    short: "体检修复目录、封面、弹窗代码与结构报错",
    desc: "适合把排版混乱、缺封面、乱码或报错的老旧 EPUB 整理成规范精排 EPUB。",
    acceptExt: ".epub",
    filterExt: ["epub"],
  },
};

const STATE_LABELS: Record<string, string> = {
  diagnosing: "正在体检与识别",
  processing: "正在排版整理",
  verifying: "正在规范质检",
  repairing: "AI 正在修补页面",
};

function fileNameOf(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function formatEncodingLabel(enc: string): string {
  const lower = (enc || "").trim().toLowerCase();
  if (!lower) return "自动识别";
  if (lower === "gb18030" || lower === "gbk" || lower === "gb2312") {
    return `中文常见旧编码 (${enc.toUpperCase()})`;
  }
  if (lower === "utf-8" || lower === "utf-8-sig") {
    return `标准通用编码 (${enc.toUpperCase()})`;
  }
  if (lower.startsWith("utf-16")) {
    return `双字节编码 (${enc.toUpperCase()})`;
  }
  if (lower === "big5") {
    return "繁体中文旧编码 (BIG5)";
  }
  return enc.toUpperCase();
}

function buildBeforeAfterItems(rec: TaskRecord): string[] {
  const items: string[] = [];
  if (rec.kind === "txt" && rec.txt_report) {
    items.push(
      rec.txt_report.mojibake_repaired
        ? `已将乱码还原为清晰汉字（原编码：${formatEncodingLabel(rec.txt_report.detected_encoding)}）`
        : `文字编码已统一为通用 UTF-8（原编码：${formatEncodingLabel(rec.txt_report.detected_encoding)}）`
    );
    if (rec.txt_report.chapter_count > 0) {
      items.push(`已自动切分并生成目录：共 ${rec.txt_report.chapter_count} 章`);
    }
    if (rec.txt_report.rules.ad_regexes.length > 0) {
      items.push(`已按 ${rec.txt_report.rules.ad_regexes.length} 条过滤规律清理广告水印`);
    }
  }
  if (rec.kind === "epub" && rec.epub_report) {
    const beforeErr =
      rec.epub_report.initial_check.fatal_count + rec.epub_report.initial_check.error_count;
    if (rec.final_check?.skipped) {
      items.push("已完成书页整理、封面补全与危险代码清理（本机无 Java，已跳过规范质检）");
    } else if (rec.final_check) {
      const afterErr = rec.final_check.fatal_count + rec.final_check.error_count;
      items.push(`结构报错变化：处理前 ${beforeErr} 处 → 处理后 ${afterErr} 处`);
    }
    if (!rec.epub_report.has_cover) {
      items.push("已为缺失封面的书籍自动补全简洁封面");
    }
  }
  if (rec.final_check?.skipped && rec.kind === "txt") {
    items.push("已跳过专业规范质检（未检测到 Java 环境，不影响正常阅读）");
  } else if (rec.final_check?.passed && !rec.final_check.skipped) {
    items.push("已通过电子书通用规范质检（0 处报错）");
  }
  return items;
}

function App() {
  const [cfg, setCfg] = useState<EngineConfig | null>(null);
  const [status, setStatus] = useState<"connecting" | "online" | "error">("connecting");
  const [info, setInfo] = useState<EngineInfo | null>(null);
  const [connErr, setConnErr] = useState("");
  const connectingRef = useRef(false);
  const browsingRef = useRef(false);
  const cfgRef = useRef<EngineConfig | null>(null);

  // 当前明确选择的任务模式（严格隔离，不擅自跨格式转换）
  const [workMode, setWorkMode] = useState<WorkMode>("txt_only");

  // 关闭确认弹窗（托盘 vs 退出）
  const [closeModalOpen, setCloseModalOpen] = useState(false);
  const [rememberCloseChoice, setRememberCloseChoice] = useState(false);

  // AI 模型设置弹窗
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [llmStatus, setLlmStatus] = useState<LlmStatus>({
    configured: false,
    masked: "",
    provider: "deepseek",
    model: "deepseek/deepseek-chat",
    base_url: "",
  });

  // 输出保存目录设置
  const [outputDirInfo, setOutputDirInfo] = useState<OutputDirSettings | null>(null);

  // 单本与批量共享状态
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uiError, setUiError] = useState("");
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [activeTaskMode, setActiveTaskMode] = useState<WorkMode>("txt_only");

  // 批量队列状态
  const [batchQueue, setBatchQueue] = useState<BatchItem[]>([]);
  const [batchStyle, setBatchStyle] = useState<StylePreset>("classic");
  const [batchFixCover, setBatchFixCover] = useState(true);
  const [batchSanitize, setBatchSanitize] = useState(true);

  // TXT 确认态
  const [txtRules, setTxtRules] = useState<TxtRules | null>(null);
  const [adRegexText, setAdRegexText] = useState("");
  const [stylePreset, setStylePreset] = useState<StylePreset>("classic");
  const [showAdvancedRules, setShowAdvancedRules] = useState(false);

  // EPUB 确认态
  const [epubTitle, setEpubTitle] = useState("");
  const [epubAuthor, setEpubAuthor] = useState("");
  const [epubLang, setEpubLang] = useState("zh-CN");
  const [fixCover, setFixCover] = useState(true);
  const [sanitizeScripts, setSanitizeScripts] = useState(true);

  const apiFetch = useCallback(
    async <T,>(path: string, init?: RequestInit, overrideCfg?: EngineConfig): Promise<T> => {
      const active = overrideCfg ?? cfgRef.current ?? cfg;
      if (!active) throw new Error("引擎尚未连接");
      const res = await fetch(`${active.baseUrl}${path}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          "X-Session-Token": active.sessionToken,
          ...(init?.headers ?? {}),
        },
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || `请求失败 (HTTP ${res.status})`);
      }
      return body as T;
    },
    [cfg]
  );

  const connectEngine = useCallback(async () => {
    if (connectingRef.current) return;
    connectingRef.current = true;
    setStatus("connecting");
    setConnErr("");
    try {
      const engineCfg = await invoke<EngineConfig>("get_engine_config");
      cfgRef.current = engineCfg;
      let lastErr = "";
      for (let i = 0; i < 120; i++) {
        try {
          const data = await apiFetch<EngineInfo>("/api/info", undefined, engineCfg);
          setCfg(engineCfg);
          setInfo(data);
          setStatus("online");
          if (data.output_dir) {
            setOutputDirInfo({
              output_dir: data.output_dir,
              is_custom: Boolean(data.output_dir_is_custom),
              default_dir: data.default_output_dir || data.output_dir,
            });
          }
          const st = await apiFetch<LlmStatus>(
            "/api/settings/api-key/status",
            undefined,
            engineCfg
          );
          setLlmStatus(st);
          return;
        } catch (e) {
          lastErr = e instanceof Error ? e.message : String(e);
        }
        await new Promise((r) => setTimeout(r, 400));
      }
      setStatus("error");
      setConnErr(lastErr || "无法连接到本地处理引擎");
    } catch (e) {
      setStatus("error");
      setConnErr(e instanceof Error ? e.message : String(e));
    } finally {
      connectingRef.current = false;
    }
  }, [apiFetch]);

  useEffect(() => {
    void connectEngine();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 监听点击右上角关闭按钮事件，询问最小化到托盘还是直接退出
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen("app://close-requested", () => {
      const saved = localStorage.getItem(CLOSE_PREF_KEY);
      if (saved === "tray") {
        void invoke("hide_to_tray");
      } else if (saved === "exit") {
        void invoke("exit_app");
      } else {
        setCloseModalOpen(true);
      }
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {
        // 浏览器调试模式忽略
      });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  const syncTaskControls = useCallback((rec: TaskRecord) => {
    if (rec.txt_report) {
      setTxtRules(rec.txt_report.rules);
      setStylePreset(rec.txt_report.rules.style_preset ?? "classic");
      setAdRegexText(rec.txt_report.rules.ad_regexes.join("\n"));
    }
    if (rec.epub_report) {
      setEpubTitle(rec.epub_report.title);
      setEpubAuthor(rec.epub_report.author);
      setEpubLang(rec.epub_report.language || "zh-CN");
    }
  }, []);

  const pollUntilSettled = useCallback(
    async (taskId: string) => {
      while (true) {
        const rec = await apiFetch<TaskRecord>(`/api/tasks/${taskId}`);
        setTask(rec);
        if (
          rec.state === "awaiting_confirm" ||
          rec.state === "completed" ||
          rec.state === "failed"
        ) {
          syncTaskControls(rec);
          return rec;
        }
        await new Promise((r) => setTimeout(r, 350));
      }
    },
    [apiFetch, syncTaskControls]
  );

  const startSingleFileInMode = useCallback(
    async (filePath: string, mode: WorkMode) => {
      setActiveTaskMode(mode);
      setBatchQueue([]);
      setBusy(true);
      setUiError("");
      try {
        const initial = await apiFetch<TaskRecord>("/api/tasks/start-diagnose", {
          method: "POST",
          body: JSON.stringify({ file_path: filePath, mode }),
        });
        setTask(initial);
        await pollUntilSettled(initial.task_id);
      } catch (e) {
        setUiError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [apiFetch, pollUntilSettled]
  );

  const enqueueOrStartSingle = useCallback(
    async (paths: string[]) => {
      if (status !== "online") {
        setUiError("处理引擎尚未就绪，请稍候再试。");
        return;
      }
      if (busy) return;

      const meta = MODE_META[workMode];
      const valid = paths.filter((p) => p.toLowerCase().endsWith(meta.acceptExt));
      const invalidCount = paths.length - valid.length;

      if (valid.length === 0) {
        setUiError(
          `当前选择了「${meta.title}」，仅接收 ${meta.acceptExt} 文件。若要处理其他格式，请先点击上方对应的模式卡片。`
        );
        return;
      }
      if (invalidCount > 0) {
        setUiError(`已跳过 ${invalidCount} 个不符合当前模式（仅限 ${meta.acceptExt}）的文件。`);
      } else {
        setUiError("");
      }

      if (batchQueue.length > 0 || valid.length > 1) {
        setTask(null);
        const targetMode = batchQueue.length > 0 ? activeTaskMode : workMode;
        if (batchQueue.length === 0) {
          setActiveTaskMode(targetMode);
        }
        setBatchQueue((prev) => {
          const existing = new Set(prev.map((it) => it.filePath));
          const additions: BatchItem[] = valid
            .filter((p) => !existing.has(p))
            .map((p, idx) => ({
              id: `${Date.now()}-${idx}-${Math.random().toString(36).slice(2, 6)}`,
              filePath: p,
              fileName: fileNameOf(p),
              status: "queued",
            }));
          return [...prev, ...additions];
        });
        return;
      }

      await startSingleFileInMode(valid[0], workMode);
    },
    [activeTaskMode, batchQueue.length, busy, startSingleFileInMode, status, workMode]
  );

  // 监听 Tauri 原生文件拖拽
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        if (event.payload.type === "over") {
          setDragging(true);
        } else if (event.payload.type === "leave") {
          setDragging(false);
        } else if (event.payload.type === "drop") {
          setDragging(false);
          const paths = event.payload.paths ?? [];
          if (paths.length > 0) {
            void enqueueOrStartSingle(paths);
          }
        }
      })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {
        // 浏览器独立调试模式下忽略
      });
    return () => {
      if (unlisten) unlisten();
    };
  }, [enqueueOrStartSingle]);

  async function handleChooseOutputDir() {
    if (status !== "online" || busy) return;
    setUiError("");
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择修复后书籍的保存文件夹",
      defaultPath: outputDirInfo?.output_dir,
    });
    if (typeof selected === "string" && selected) {
      try {
        const res = await apiFetch<OutputDirSettings>("/api/settings/output-dir", {
          method: "POST",
          body: JSON.stringify({ output_dir: selected }),
        });
        setOutputDirInfo(res);
      } catch (e) {
        setUiError(e instanceof Error ? e.message : String(e));
      }
    }
  }

  async function handleResetOutputDir() {
    if (status !== "online" || busy) return;
    setUiError("");
    try {
      const res = await apiFetch<OutputDirSettings>("/api/settings/output-dir", {
        method: "POST",
        body: JSON.stringify({ output_dir: "" }),
      });
      setOutputDirInfo(res);
    } catch (e) {
      setUiError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleBrowseFile() {
    if (busy || browsingRef.current) return;
    browsingRef.current = true;
    setUiError("");
    const meta = MODE_META[workMode];
    try {
      const selected = await open({
        multiple: true,
        title: `选择要处理的 ${meta.acceptExt} 文件（${meta.title}）`,
        filters: [{ name: `${meta.acceptExt.toUpperCase()} 文件`, extensions: meta.filterExt }],
      });
      if (Array.isArray(selected) && selected.length > 0) {
        await enqueueOrStartSingle(selected);
      } else if (typeof selected === "string" && selected) {
        await enqueueOrStartSingle([selected]);
      }
    } finally {
      browsingRef.current = false;
    }
  }

  async function handleRunBatch() {
    if (busy || batchQueue.length === 0) return;
    setBusy(true);
    setUiError("");

    const updateItem = (id: string, patch: Partial<BatchItem>) => {
      setBatchQueue((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
    };

    try {
      for (const item of batchQueue) {
        if (item.status === "done") continue;
        try {
          updateItem(item.id, { status: "diagnosing", error: undefined });
          const started = await apiFetch<TaskRecord>("/api/tasks/start-diagnose", {
            method: "POST",
            body: JSON.stringify({ file_path: item.filePath, mode: activeTaskMode }),
          });
          setTask(started);
          const diag = await pollUntilSettled(started.task_id);
          if (diag.state === "failed") {
            updateItem(item.id, { status: "error", error: diag.error || "处理被拦截" });
            continue;
          }

          updateItem(item.id, { status: "processing" });
          if (activeTaskMode === "txt_only") {
            const exported = await apiFetch<TaskRecord>(
              `/api/tasks/${diag.task_id}/export-utf8-txt`,
              {
                method: "POST",
                body: JSON.stringify({ title: diag.txt_report?.rules.title || undefined }),
              }
            );
            setTask(exported);
            updateItem(item.id, {
              status: "done",
              outputPath: exported.utf8_txt_output_path || undefined,
              summary: `已修复为纯净 UTF-8 TXT（原编码：${formatEncodingLabel(diag.txt_report?.detected_encoding ?? "")}）`,
            });
            continue;
          }

          let finalRec = diag;
          if (diag.kind === "txt" && diag.txt_report) {
            const confirmStarted = await apiFetch<TaskRecord>(
              `/api/tasks/${diag.task_id}/start-confirm-txt`,
              {
                method: "POST",
                body: JSON.stringify({
                  ...diag.txt_report.rules,
                  style_preset: batchStyle,
                }),
              }
            );
            setTask(confirmStarted);
            finalRec = await pollUntilSettled(diag.task_id);
          } else if (diag.kind === "epub" && diag.epub_report) {
            const confirmStarted = await apiFetch<TaskRecord>(
              `/api/tasks/${diag.task_id}/start-confirm-epub`,
              {
                method: "POST",
                body: JSON.stringify({
                  title: diag.epub_report.title || item.fileName,
                  author: diag.epub_report.author || "佚名",
                  language: diag.epub_report.language || "zh-CN",
                  fix_cover: batchFixCover,
                  sanitize_scripts: batchSanitize,
                }),
              }
            );
            setTask(confirmStarted);
            finalRec = await pollUntilSettled(diag.task_id);
          }

          if (finalRec.state === "completed") {
            const summaryText = buildBeforeAfterItems(finalRec).slice(0, 2).join("；");
            updateItem(item.id, {
              status: "done",
              outputPath: finalRec.output_path || undefined,
              summary: summaryText,
            });
          } else {
            updateItem(item.id, { status: "error", error: finalRec.error || "生成失败" });
          }
        } catch (e) {
          updateItem(item.id, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        }
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleExportUtf8Txt() {
    if (!task) return;
    setBusy(true);
    setUiError("");
    try {
      const updated = await apiFetch<TaskRecord>(`/api/tasks/${task.task_id}/export-utf8-txt`, {
        method: "POST",
        body: JSON.stringify({ title: txtRules?.title || undefined }),
      });
      setTask(updated);
    } catch (e) {
      setUiError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleContinueToEpub(cleanTxtPath: string) {
    setWorkMode("txt_to_epub");
    await startSingleFileInMode(cleanTxtPath, "txt_to_epub");
  }

  async function handleConfirmTxt() {
    if (!task || !txtRules) return;
    setBusy(true);
    setUiError("");
    try {
      const payload: TxtRules = {
        ...txtRules,
        style_preset: stylePreset,
        ad_regexes: adRegexText
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean),
      };
      const started = await apiFetch<TaskRecord>(`/api/tasks/${task.task_id}/start-confirm-txt`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setTask(started);
      await pollUntilSettled(task.task_id);
    } catch (e) {
      setUiError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmEpub() {
    if (!task) return;
    setBusy(true);
    setUiError("");
    try {
      const started = await apiFetch<TaskRecord>(
        `/api/tasks/${task.task_id}/start-confirm-epub`,
        {
          method: "POST",
          body: JSON.stringify({
            title: epubTitle.trim() || "未命名书籍",
            author: epubAuthor.trim() || "佚名",
            language: epubLang.trim() || "zh-CN",
            fix_cover: fixCover,
            sanitize_scripts: sanitizeScripts,
          }),
        }
      );
      setTask(started);
      await pollUntilSettled(task.task_id);
    } catch (e) {
      setUiError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleReveal(path: string) {
    try {
      await invoke("reveal_in_explorer", { path });
    } catch (e) {
      setUiError(e instanceof Error ? e.message : String(e));
    }
  }

  function handleReset() {
    setTask(null);
    setTxtRules(null);
    setBatchQueue([]);
    setUiError("");
  }

  async function handleCloseChoice(action: "tray" | "exit") {
    if (rememberCloseChoice) {
      localStorage.setItem(CLOSE_PREF_KEY, action);
    }
    setCloseModalOpen(false);
    if (action === "tray") {
      await invoke("hide_to_tray");
    } else {
      await invoke("exit_app");
    }
  }

  const txtPreviews = task?.txt_report?.previews ?? [];
  const visiblePreviews =
    txtPreviews.length > 25
      ? [...txtPreviews.slice(0, 20), ...txtPreviews.slice(-5)]
      : txtPreviews;
  const activeMeta = MODE_META[workMode];

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo-badge">EPUB</span>
          <div>
            <h1>EPUB 精排工厂</h1>
            <p className="subtitle">乱码还原 · 自动分章 · 电子书一键精排工作台</p>
          </div>
        </div>

        <div className="topbar-actions">
          {(task || batchQueue.length > 0) && (
            <button className="btn btn-secondary" onClick={handleReset} disabled={busy}>
              返回首页
            </button>
          )}
          <button
            className={`btn ${llmStatus.configured ? "btn-secondary" : "btn-outline-warn"}`}
            onClick={() => setKeyModalOpen(true)}
            disabled={status !== "online"}
          >
            {llmStatus.configured
              ? `AI 模型：${llmStatus.model}`
              : "AI 模型设置（可选）"}
          </button>

          <div className={`status-pill status-${status}`}>
            <span className="dot" />
            {status === "connecting" && "正在准备处理引擎…"}
            {status === "online" && `已就绪 v${info?.version ?? ""}`}
            {status === "error" && (
              <>
                离线
                <button className="retry-btn" onClick={connectEngine}>
                  重试
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      {status === "connecting" && (
        <div className="info-banner">
          正在首次解压并启动本地处理引擎，首次打开通常需要 10~20 秒，请稍候…
        </div>
      )}
      {connErr && status === "error" && (
        <div className="error-banner">连接本地引擎失败：{connErr}</div>
      )}
      {uiError && <div className="error-banner">{uiError}</div>}

      <div className="main-grid">
        <main className="work-area">
          {!task && batchQueue.length === 0 && (
            <>
              {/* 明确区分三种处理模式，杜绝误把 TXT 自动变 EPUB */}
              <section className="mode-grid">
                {(Object.keys(MODE_META) as WorkMode[]).map((mKey) => {
                  const m = MODE_META[mKey];
                  const selected = workMode === mKey;
                  return (
                    <button
                      key={mKey}
                      type="button"
                      className={`mode-card ${selected ? "mode-card-active" : ""}`}
                      onClick={() => {
                        setWorkMode(mKey);
                        setUiError("");
                      }}
                    >
                      <strong>{m.title}</strong>
                      <span className="mode-short">{m.short}</span>
                    </button>
                  );
                })}
              </section>

              <section
                className={`dropzone ${dragging ? "dropzone-active" : ""}`}
                onClick={handleBrowseFile}
              >
                <div className="drop-icon">{busy ? "⏳" : "📚"}</div>
                <h2>
                  {busy
                    ? "正在检查文件，请稍候…"
                    : dragging
                      ? `松开鼠标导入（${activeMeta.title}）`
                      : `拖入一个或多个 ${activeMeta.acceptExt} 文件，或点击选择`}
                </h2>
                <p className="drop-hint">{activeMeta.desc}</p>
                <p className="safety-note">
                  当前模式仅接收 <code>{activeMeta.acceptExt}</code> · 原文件绝不改动，新书会保存到下方指定的输出文件夹中
                </p>
              </section>

              <section className="output-dir-card">
                <div className="output-dir-info">
                  <div className="output-dir-title-row">
                    <strong>📁 修好后的书籍保存位置</strong>
                    <span className={`tag ${outputDirInfo?.is_custom ? "tag-info" : "tag-ok"}`}>
                      {outputDirInfo?.is_custom
                        ? "已自定义位置"
                        : "默认：程序所在文件夹 / 精排输出书籍"}
                    </span>
                  </div>
                  <p className="output-path">
                    {outputDirInfo?.output_dir || "引擎就绪后将自动在程序所在文件夹内创建「精排输出书籍」"}
                  </p>
                  <p className="output-dir-guide">
                    提示：未单独设置时，程序会自动在自身所在目录下新建「精排输出书籍」文件夹来存放成果；您也可以随时点击右侧“更改保存位置”放到桌面或任意硬盘目录。
                  </p>
                </div>
                <div className="output-dir-actions">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={handleChooseOutputDir}
                    disabled={status !== "online" || busy}
                  >
                    更改保存位置
                  </button>
                  {outputDirInfo?.output_dir && (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => handleReveal(outputDirInfo.output_dir)}
                    >
                      打开保存文件夹
                    </button>
                  )}
                  {outputDirInfo?.is_custom && (
                    <button
                      type="button"
                      className="btn-link btn-link-muted"
                      onClick={handleResetOutputDir}
                      disabled={busy}
                    >
                      恢复默认
                    </button>
                  )}
                </div>
              </section>
            </>
          )}

          {/* 批量处理队列视图 */}
          {batchQueue.length > 0 && (
            <section className="card">
              <div className="card-header">
                <div>
                  <h2>
                    批量处理队列 · {MODE_META[activeTaskMode].title}（已完成{" "}
                    {batchQueue.filter((it) => it.status === "done").length} / {batchQueue.length}{" "}
                    本）
                  </h2>
                  <p className="muted-path">
                    {MODE_META[activeTaskMode].short} · 成果将统一保存到：
                    {outputDirInfo?.output_dir || "程序所在文件夹/精排输出书籍"}
                  </p>
                </div>
                <div className="batch-header-actions">
                  <button
                    className="btn btn-secondary"
                    onClick={handleBrowseFile}
                    disabled={busy}
                  >
                    + 继续添加文件
                  </button>
                  <button className="btn btn-primary" onClick={handleRunBatch} disabled={busy}>
                    {busy
                      ? "批量处理中…"
                      : activeTaskMode === "txt_only"
                        ? "一键批量修复为正常 TXT"
                        : "一键开始批量精排"}
                  </button>
                </div>
              </div>

              {(busy || batchQueue.some((it) => it.status === "done")) && (
                <div className="batch-progress-track">
                  <div
                    className="batch-progress-fill"
                    style={{
                      width: `${Math.round(
                        (batchQueue.filter((it) => it.status === "done" || it.status === "error")
                          .length /
                          batchQueue.length) *
                          100
                      )}%`,
                    }}
                  />
                </div>
              )}

              {activeTaskMode !== "txt_only" && (
                <div className="form-grid">
                  {activeTaskMode === "txt_to_epub" && (
                    <label className="field-label">
                      TXT 排版风格（免费开源字体声明）
                      <select
                        className="text-input"
                        value={batchStyle}
                        onChange={(e) => setBatchStyle(e.target.value as StylePreset)}
                        disabled={busy}
                      >
                        <option value="classic">典雅人文（文楷 / 思源宋体）</option>
                        <option value="modern">现代简约（思源黑体）</option>
                        <option value="comfort">护眼大字（大字号 + 宽行距）</option>
                      </select>
                    </label>
                  )}
                  {activeTaskMode === "epub_refine" && (
                    <div className="checkbox-col">
                      <label>
                        <input
                          type="checkbox"
                          checked={batchFixCover}
                          onChange={(e) => setBatchFixCover(e.target.checked)}
                          disabled={busy}
                        />
                        缺失封面时自动补全简洁封面
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={batchSanitize}
                          onChange={(e) => setBatchSanitize(e.target.checked)}
                          disabled={busy}
                        />
                        自动清理书中隐患网页代码
                      </label>
                    </div>
                  )}
                </div>
              )}

              <ul className="batch-list">
                {batchQueue.map((item) => (
                  <li key={item.id} className={`batch-item batch-${item.status}`}>
                    <div className="batch-main">
                      <strong>{item.fileName}</strong>
                      {item.summary && <p className="batch-sub">{item.summary}</p>}
                      {item.error && <p className="batch-err">{item.error}</p>}
                    </div>
                    <div className="batch-right">
                      <span className="tag">
                        {item.status === "queued" && "排队中"}
                        {item.status === "diagnosing" && "检查中"}
                        {item.status === "processing" && "处理中"}
                        {item.status === "done" && "已完成"}
                        {item.status === "error" && "失败"}
                      </span>
                      {item.outputPath && (
                        <button
                          className="btn-link"
                          onClick={() => handleReveal(item.outputPath!)}
                        >
                          打开位置
                        </button>
                      )}
                      {!busy && (
                        <button
                          className="btn-link btn-link-muted"
                          onClick={() =>
                            setBatchQueue((prev) => prev.filter((it) => it.id !== item.id))
                          }
                        >
                          移除
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 单本后台处理过渡视图 */}
          {batchQueue.length === 0 &&
            task &&
            (task.state === "diagnosing" ||
              task.state === "processing" ||
              task.state === "verifying" ||
              task.state === "repairing") && (
              <section className="card">
                <div className="card-header">
                  <div>
                    <h2>正在处理：{fileNameOf(task.source_path)}</h2>
                    <p className="muted-path">{task.source_path}</p>
                  </div>
                  <span className="tag tag-info">{STATE_LABELS[task.state] || task.state}</span>
                </div>
                <p className="diag-placeholder">
                  正在安全读取文件并执行处理步骤，可以在右侧查看每一步实时进展…
                </p>
              </section>
            )}

          {/* 模式一：仅修复乱码 TXT → 正常 UTF-8 TXT（绝不擅自转成 EPUB） */}
          {batchQueue.length === 0 &&
            activeTaskMode === "txt_only" &&
            task?.state === "awaiting_confirm" &&
            task.txt_report &&
            txtRules && (
              <section className="card">
                <div className="card-header">
                  <div>
                    <h2>乱码检查与纯净 TXT 导出</h2>
                    <p className="muted-path">{task.source_path}</p>
                  </div>
                  <span
                    className={`tag ${task.txt_report.mojibake_repaired ? "tag-ok" : "tag-info"}`}
                  >
                    原编码：{formatEncodingLabel(task.txt_report.detected_encoding)}
                  </span>
                </div>

                {task.txt_report.warnings.length > 0 && (
                  <div className="warn-box">
                    {task.txt_report.warnings.map((w, i) => (
                      <p key={i}>⚠ {w}</p>
                    ))}
                  </div>
                )}

                <div className="preview-box">
                  <div className="preview-label">
                    修复后汉字预览（确认中文显示清晰，无“锟斤拷/烫烫烫”乱码；若此处仍显示异常，请勿勉强保存）
                  </div>
                  <pre className="preview-text">{task.txt_report.sample_text_preview}</pre>
                </div>

                <label className="field-label">
                  输出文件书名（将自动另存为「书名_UTF8.txt」，原文件不变）
                  <input
                    className="text-input"
                    value={txtRules.title}
                    onChange={(e) => setTxtRules({ ...txtRules, title: e.target.value })}
                  />
                </label>

                <div className="action-bar">
                  <button className="btn btn-primary" onClick={handleExportUtf8Txt} disabled={busy}>
                    {busy ? "正在保存…" : "保存为正常 UTF-8 简体中文 (.txt)"}
                  </button>
                </div>

                {task.utf8_txt_output_path && (
                  <div className="summary-card">
                    <div className="preview-label">✅ 纯净 UTF-8 TXT 已保存成功（未转换为 EPUB）</div>
                    <p className="output-path">{task.utf8_txt_output_path}</p>
                    <div className="action-bar">
                      <button
                        className="btn btn-secondary"
                        onClick={() => handleReveal(task.utf8_txt_output_path!)}
                      >
                        打开所在文件夹
                      </button>
                      <button
                        className="btn btn-primary"
                        onClick={() => handleContinueToEpub(task.utf8_txt_output_path!)}
                      >
                        继续将这本正常 TXT 精排成 EPUB →
                      </button>
                    </div>
                  </div>
                )}
              </section>
            )}

          {/* 模式二：TXT 一步精排为 EPUB */}
          {batchQueue.length === 0 &&
            activeTaskMode === "txt_to_epub" &&
            task?.state === "awaiting_confirm" &&
            task.txt_report &&
            txtRules && (
              <section className="card">
                <div className="card-header">
                  <div>
                    <h2>TXT 检查报告与精排 EPUB 设置</h2>
                    <p className="muted-path">{task.source_path}</p>
                  </div>
                  <span
                    className={`tag ${task.txt_report.mojibake_repaired ? "tag-ok" : "tag-info"}`}
                  >
                    原编码：{formatEncodingLabel(task.txt_report.detected_encoding)}
                  </span>
                </div>

                <div className="stats-row">
                  <div className="stat-box">
                    <span>识别章节</span>
                    <strong>{task.txt_report.chapter_count} 章</strong>
                  </div>
                  <div className="stat-box">
                    <span>全书字数</span>
                    <strong>{task.txt_report.total_chars.toLocaleString()}</strong>
                  </div>
                  <div className="stat-box">
                    <span>识别方式</span>
                    <strong>{task.txt_report.used_llm ? "AI 辅助识别" : "本地内置规则"}</strong>
                  </div>
                </div>

                {task.txt_report.warnings.length > 0 && (
                  <div className="warn-box">
                    {task.txt_report.warnings.map((w, i) => (
                      <p key={i}>⚠ {w}</p>
                    ))}
                  </div>
                )}

                <div className="preview-box">
                  <div className="preview-label">
                    正文开头预览（请确认汉字显示清晰，没有乱码）
                  </div>
                  <pre className="preview-text">{task.txt_report.sample_text_preview}</pre>
                </div>

                <div className="form-grid">
                  <label className="field-label">
                    书名
                    <input
                      className="text-input"
                      value={txtRules.title}
                      onChange={(e) => setTxtRules({ ...txtRules, title: e.target.value })}
                    />
                  </label>
                  <label className="field-label">
                    作者
                    <input
                      className="text-input"
                      value={txtRules.author}
                      onChange={(e) => setTxtRules({ ...txtRules, author: e.target.value })}
                    />
                  </label>
                  <label className="field-label">
                    排版风格（免费开源字体）
                    <select
                      className="text-input"
                      value={stylePreset}
                      onChange={(e) => setStylePreset(e.target.value as StylePreset)}
                    >
                      <option value="classic">典雅人文（文楷 / 思源宋体）</option>
                      <option value="modern">现代简约（思源黑体）</option>
                      <option value="comfort">护眼大字（大字号 + 宽行距）</option>
                    </select>
                  </label>
                </div>

                <div>
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() => setShowAdvancedRules((v) => !v)}
                  >
                    {showAdvancedRules ? "收起高级排版微调 ▴" : "高级排版微调（一般不用改） ▾"}
                  </button>
                </div>

                {showAdvancedRules && (
                  <div className="advanced-box">
                    <p className="muted-path">
                      如果不懂下面的匹配符号，保持默认即可，直接点下方按钮生成。
                    </p>
                    <div className="form-grid">
                      <label className="field-label">
                        章节标题识别规则
                        <input
                          className="text-input mono"
                          value={txtRules.chapter_regex}
                          onChange={(e) =>
                            setTxtRules({ ...txtRules, chapter_regex: e.target.value })
                          }
                        />
                      </label>
                      <label className="field-label">
                        分卷标题识别规则（可留空）
                        <input
                          className="text-input mono"
                          value={txtRules.volume_regex}
                          onChange={(e) =>
                            setTxtRules({ ...txtRules, volume_regex: e.target.value })
                          }
                        />
                      </label>
                    </div>
                    <label className="field-label">
                      自动删掉这些推广句子/网址（每行一条，支持普通词语）
                      <textarea
                        className="text-input mono"
                        rows={2}
                        value={adRegexText}
                        onChange={(e) => setAdRegexText(e.target.value)}
                      />
                    </label>
                  </div>
                )}

                {visiblePreviews.length > 0 && (
                  <div className="chapters-preview">
                    <div className="preview-label">
                      目录试排预览
                      {txtPreviews.length > 25
                        ? `（共 ${txtPreviews.length} 项预览，仅展示首尾代表章节以保持流畅）`
                        : `（前 ${visiblePreviews.length} 章）`}
                    </div>
                    <ul>
                      {visiblePreviews.map((ch) => (
                        <li key={ch.index}>
                          <span className="ch-title">
                            {ch.volume ? `[${ch.volume}] ` : ""}
                            {ch.title}
                          </span>
                          <span className="ch-len">{ch.char_count} 字</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="action-bar">
                  <button
                    className="btn btn-secondary"
                    onClick={handleExportUtf8Txt}
                    disabled={busy}
                  >
                    顺便也保存一份纯净 TXT
                  </button>
                  <button className="btn btn-primary" onClick={handleConfirmTxt} disabled={busy}>
                    {busy ? "正在排版与质检…" : "确认并生成精排 EPUB"}
                  </button>
                </div>

                {task.utf8_txt_output_path && (
                  <div className="inline-result">
                    <span>✅ 纯净文本已保存：{task.utf8_txt_output_path}</span>
                    <button
                      className="btn-link"
                      onClick={() => handleReveal(task.utf8_txt_output_path!)}
                    >
                      打开所在文件夹
                    </button>
                  </div>
                )}
              </section>
            )}

          {/* 模式三：EPUB 修复确认台 */}
          {batchQueue.length === 0 &&
            activeTaskMode === "epub_refine" &&
            task?.state === "awaiting_confirm" &&
            task.epub_report && (
              <section className="card">
                <div className="card-header">
                  <div>
                    <h2>EPUB 健康体检与修复确认</h2>
                    <p className="muted-path">{task.source_path}</p>
                  </div>
                  <span
                    className={`tag ${
                      task.epub_report.initial_check.skipped ||
                      task.epub_report.initial_check.passed
                        ? "tag-ok"
                        : "tag-warn"
                    }`}
                  >
                    {task.epub_report.initial_check.skipped
                      ? "基础体检完成"
                      : `质检发现 ${
                          task.epub_report.initial_check.fatal_count +
                          task.epub_report.initial_check.error_count
                        } 处报错`}
                  </span>
                </div>

                {task.epub_report.issues.length > 0 ? (
                  <ul className="issue-list">
                    {task.epub_report.issues.map((iss) => (
                      <li key={iss.code} className={`issue-item sev-${iss.severity}`}>
                        <strong>{iss.label}</strong>
                        <span>{iss.description}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ok-box">这本书结构较完整，可直接整理规范后重新打包。</p>
                )}

                <div className="form-grid">
                  <label className="field-label">
                    书名
                    <input
                      className="text-input"
                      value={epubTitle}
                      onChange={(e) => setEpubTitle(e.target.value)}
                    />
                  </label>
                  <label className="field-label">
                    作者
                    <input
                      className="text-input"
                      value={epubAuthor}
                      onChange={(e) => setEpubAuthor(e.target.value)}
                    />
                  </label>
                  <label className="field-label">
                    书籍语言
                    <select
                      className="text-input"
                      value={epubLang}
                      onChange={(e) => setEpubLang(e.target.value)}
                    >
                      <option value="zh-CN">简体中文 (zh-CN)</option>
                      <option value="zh-TW">繁体中文 (zh-TW)</option>
                      <option value="en">英文 (en)</option>
                      {!["zh-CN", "zh-TW", "en"].includes(epubLang) && (
                        <option value={epubLang}>{epubLang}</option>
                      )}
                    </select>
                  </label>
                </div>

                <div className="checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={fixCover}
                      onChange={(e) => setFixCover(e.target.checked)}
                    />
                    缺少封面时自动补全简洁封面
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={sanitizeScripts}
                      onChange={(e) => setSanitizeScripts(e.target.checked)}
                    />
                    清理书里夹带的网页弹窗和隐患代码
                  </label>
                </div>

                <div className="action-bar">
                  <button className="btn btn-primary" onClick={handleConfirmEpub} disabled={busy}>
                    {busy ? "正在精排与自动修补…" : "确认并开始精排修复"}
                  </button>
                </div>
              </section>
            )}

          {/* 单本完成 / 失败结果卡片 */}
          {batchQueue.length === 0 &&
            task &&
            (task.state === "completed" || task.state === "failed") && (
              <section className="card">
                <div className="card-header">
                  <h2>{task.state === "completed" ? "🎉 精排处理完成" : "❌ 处理已停止"}</h2>
                  {task.final_check && (
                    <span
                      className={`tag ${
                        task.final_check.skipped || task.final_check.passed ? "tag-ok" : "tag-warn"
                      }`}
                    >
                      {task.final_check.skipped
                        ? "已完成基础精排（跳过质检）"
                        : task.final_check.passed
                          ? "规范质检 0 报错通过"
                          : `剩余 ${
                              task.final_check.fatal_count + task.final_check.error_count
                            } 处报错`}
                    </span>
                  )}
                </div>

                {task.error && <p className="error-banner">{task.error}</p>}

                {task.state === "completed" && (
                  <div className="summary-card">
                    <div className="preview-label">本次处理前后变化</div>
                    <ul>
                      {buildBeforeAfterItems(task).map((line, i) => (
                        <li key={i}>✔ {line}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {task.output_path && (
                  <div className="output-box">
                    <div>
                      <span className="preview-label">精排电子书保存位置</span>
                      <p className="output-path">{task.output_path}</p>
                    </div>
                    <button
                      className="btn btn-primary"
                      onClick={() => handleReveal(task.output_path!)}
                    >
                      打开所在文件夹
                    </button>
                  </div>
                )}

                {task.utf8_txt_output_path && (
                  <div className="output-box">
                    <div>
                      <span className="preview-label">纯净文本保存位置</span>
                      <p className="output-path">{task.utf8_txt_output_path}</p>
                    </div>
                    <button
                      className="btn btn-secondary"
                      onClick={() => handleReveal(task.utf8_txt_output_path!)}
                    >
                      打开所在文件夹
                    </button>
                  </div>
                )}

                {task.final_check &&
                  !task.final_check.skipped &&
                  !task.final_check.passed &&
                  task.final_check.messages.length > 0 && (
                    <div className="warn-box">
                      {task.final_check.messages.slice(0, 6).map((m, idx) => (
                        <p key={idx}>
                          [{m.id}] {m.file_path}: {m.message}
                        </p>
                      ))}
                    </div>
                  )}

                <div className="action-bar">
                  <button className="btn btn-secondary" onClick={handleReset} disabled={busy}>
                    继续处理下一本
                  </button>
                </div>
              </section>
            )}
        </main>

        <TimelinePanel events={task?.events ?? []} busy={busy} />
      </div>

      {keyModalOpen && (
        <ApiKeyModal
          status={llmStatus}
          onSave={async (payload) => {
            const res = await apiFetch<LlmStatus>("/api/settings/api-key", {
              method: "POST",
              body: JSON.stringify(payload),
            });
            setLlmStatus(res);
          }}
          onFetchModels={async (payload) => {
            const res = await apiFetch<{ models: string[]; count: number }>(
              "/api/settings/models",
              {
                method: "POST",
                body: JSON.stringify(payload),
              }
            );
            return res.models;
          }}
          onDelete={async () => {
            const res = await apiFetch<LlmStatus>("/api/settings/api-key", {
              method: "DELETE",
            });
            setLlmStatus(res);
          }}
          onClose={() => setKeyModalOpen(false)}
        />
      )}

      {/* 点击右上角关闭时的确认弹窗：最小化到系统托盘 vs 直接退出 */}
      {closeModalOpen && (
        <div className="modal-backdrop" onClick={() => setCloseModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>关闭确认</h3>
              <button
                className="icon-close"
                onClick={() => setCloseModalOpen(false)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
            <p className="modal-desc">
              您点击了右上角的关闭按钮，希望将软件<strong>最小化到右下角系统托盘</strong>继续常驻，还是<strong>直接退出程序</strong>？
            </p>
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={rememberCloseChoice}
                onChange={(e) => setRememberCloseChoice(e.target.checked)}
              />
              记住我的选择（下次不再询问）
            </label>
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setCloseModalOpen(false)}>
                取消
              </button>
              <div className="spacer" />
              <button className="btn btn-danger-outline" onClick={() => handleCloseChoice("exit")}>
                直接退出程序
              </button>
              <button className="btn btn-primary" onClick={() => handleCloseChoice("tray")}>
                最小化到系统托盘
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
