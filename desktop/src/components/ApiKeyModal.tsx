import { useMemo, useState } from "react";
import type { LlmStatus } from "../types";

interface ApiKeyModalProps {
  status: LlmStatus;
  onSave: (payload: {
    provider: string;
    model: string;
    base_url: string;
    api_key: string;
  }) => Promise<void>;
  onFetchModels: (payload: {
    provider: string;
    base_url: string;
    api_key: string;
  }) => Promise<string[]>;
  onDelete: () => Promise<void>;
  onClose: () => void;
}

const DEFAULT_URLS: Record<string, string> = {
  deepseek: "https://api.deepseek.com",
  ollama: "http://127.0.0.1:11434",
  custom: "https://api.openai.com/v1",
};

export function ApiKeyModal({
  status,
  onSave,
  onFetchModels,
  onDelete,
  onClose,
}: ApiKeyModalProps) {
  const initialProvider = status.provider || "deepseek";
  const [provider, setProvider] = useState(initialProvider);
  const [model, setModel] = useState(
    (status.model || "deepseek-chat").replace(/^deepseek\//, "")
  );
  const [baseUrl, setBaseUrl] = useState(
    status.base_url || DEFAULT_URLS[initialProvider] || DEFAULT_URLS.deepseek
  );
  const [apiKey, setApiKey] = useState("");
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchHint, setFetchHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const filteredModels = useMemo(() => {
    const q = model.trim().toLowerCase();
    if (!q || fetchedModels.includes(model.trim())) {
      return fetchedModels;
    }
    const matched = fetchedModels.filter((m) => m.toLowerCase().includes(q));
    return matched.length > 0 ? matched : fetchedModels;
  }, [fetchedModels, model]);

  function handleProviderChange(next: string) {
    setProvider(next);
    setFetchedModels([]);
    setFetchHint("");
    setErr("");
    setDropdownOpen(false);
    setBaseUrl(DEFAULT_URLS[next] || "");
    if (next === "deepseek") {
      setModel("deepseek-chat");
    } else if (next === "ollama") {
      setModel("qwen2.5:7b");
    } else {
      setModel("");
    }
  }

  async function handleFetchModels() {
    setFetchingModels(true);
    setErr("");
    setFetchHint("");
    try {
      const list = await onFetchModels({
        provider,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      });
      setFetchedModels(list);
      setDropdownOpen(true);
      setFetchHint(`已连接成功，共识别到 ${list.length} 个可用模型`);
      if (!model.trim() && list.length > 0) {
        setModel(list[0]);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setFetchingModels(false);
    }
  }

  async function handleSave() {
    if (provider !== "ollama" && !apiKey.trim() && !status.configured) {
      setErr("请填写有效的访问密钥（API Key）");
      return;
    }
    if (!baseUrl.trim()) {
      setErr("请填写服务地址（URL）");
      return;
    }
    if (!model.trim()) {
      setErr("请选择或输入要使用的模型名称");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await onSave({
        provider,
        model: model.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    setErr("");
    try {
      await onDelete();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>AI 智能助手设置</h3>
          <button className="icon-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <div className="modal-feature-box">
          <strong>配置 AI 模型有什么用？（和不用 AI 的区别）</strong>
          <ul>
            <li>
              <b>不用 AI 也能排</b>：纯转正常 UTF-8 TXT 完全不需要 AI；内置规则也能免费处理“第X章”等常见小说的常规切分与基础排版。
            </li>
            <li>
              <b>配了更聪明（TXT 转精排 EPUB）</b>：遇到“卷一·xxx”“【番外】”等非标准章节标题，或正文夹杂网站求票推广语时，AI 会智能识别本书专属章节规律、自动归纳书名/作者并精准剔除广告。
            </li>
            <li>
              <b>坏书自动修（EPUB 深度精修）</b>：当旧 EPUB 结构错乱、校验报错无法正常阅读时，AI 会自动定位损坏的章节或样式文件并尝试重写修复。
            </li>
          </ul>
        </div>

        <p className="modal-desc">
          不配置也能免费使用内置规则完成基础精排。连接云端 AI 时，仅截取<strong>少量章节标题与出错片段</strong>（自动对邮箱、手机号、密钥等做脱敏处理），绝不上传整本书；密钥加密保存在 Windows 系统凭据管理器中。
          <br />
          <span className="text-muted">
            独立声明：本工具与 DeepSeek 等模型服务商无官方合作或从属关系，使用云端接口需自行申请密钥并遵守对应服务商开放平台协议。
          </span>
        </p>

        <div className="key-status-box">
          <span>当前状态：</span>
          {status.configured ? (
            <strong className="text-ok">
              已启用 {status.provider} · {status.model}
              {status.masked ? ` (${status.masked})` : ""}
            </strong>
          ) : (
            <span className="text-muted">未配置（当前使用内置本地规则）</span>
          )}
        </div>

        <label className="field-label">
          选择 AI 服务方式
          <select
            className="text-input"
            value={provider}
            onChange={(e) => handleProviderChange(e.target.value)}
          >
            <option value="deepseek">DeepSeek 官方（填入密钥即可获取最新模型）</option>
            <option value="custom">通用接口 / 第三方中转站（OpenAI 兼容格式）</option>
            <option value="ollama">本地 Ollama 模型（完全离线、零费用）</option>
          </select>
        </label>

        <label className="field-label">
          服务地址（URL，可直接修改）
          <input
            className="text-input mono"
            placeholder="例如 https://api.deepseek.com 或 https://api.example.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>

        {provider !== "ollama" && (
          <label className="field-label">
            访问密钥（API Key）
            {status.configured ? "（已保存旧密钥，不改可留空）" : ""}
            <input
              type="password"
              className="text-input"
              placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
        )}

        <div className="field-label">
          <div className="model-label-row">
            <span>选择或输入模型</span>
            <button
              type="button"
              className="btn-link"
              onClick={handleFetchModels}
              disabled={fetchingModels || busy}
            >
              {fetchingModels ? "正在读取模型…" : "↻ 从当前地址读取模型列表"}
            </button>
          </div>

          {fetchHint && (
            <span className="text-ok model-hint">
              {dropdownOpen
                ? `${fetchHint}，请从下方列表点选：`
                : `${fetchHint}，当前已选：${model.trim() || "（未选择）"}`}
            </span>
          )}

          <div className="model-combo">
            <div className="model-input-wrap">
              <input
                className="text-input mono model-combo-input"
                placeholder="点右侧倒三角选择，或输入关键词筛选 / 手动填写"
                value={model}
                onFocus={() => {
                  if (fetchedModels.length > 0) setDropdownOpen(true);
                }}
                onChange={(e) => {
                  setModel(e.target.value);
                  if (fetchedModels.length > 0) setDropdownOpen(true);
                }}
              />
              <button
                type="button"
                className="model-arrow-btn"
                aria-label="展开模型列表"
                title={fetchedModels.length > 0 ? "展开或收起模型列表" : "点击先读取模型列表"}
                onClick={() => {
                  if (fetchedModels.length === 0) {
                    void handleFetchModels();
                  } else {
                    setDropdownOpen((v) => !v);
                  }
                }}
              >
                ▾
              </button>
            </div>

            {dropdownOpen && filteredModels.length > 0 && (
              <ul className="model-dropdown">
                {filteredModels.map((item) => (
                  <li key={item}>
                    <button
                      type="button"
                      className={`model-option ${item === model ? "model-option-active" : ""}`}
                      onClick={() => {
                        setModel(item);
                        setDropdownOpen(false);
                      }}
                    >
                      {item}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {err && <p className="error-banner">{err}</p>}

        <div className="modal-actions">
          {status.configured && (
            <button className="btn btn-danger-outline" onClick={handleDelete} disabled={busy}>
              清除设置
            </button>
          )}
          <div className="spacer" />
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={busy}>
            {busy ? "保存中…" : "保存并启用"}
          </button>
        </div>
      </div>
    </div>
  );
}
