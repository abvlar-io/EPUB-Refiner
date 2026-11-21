"""安全存储大模型配置与 API Key（密钥优先使用 Windows 系统凭据管理器）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "com.epubrefiner.desktop"
DEEPSEEK_ACCOUNT = "deepseek_api_key"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def normalize_base_url(provider: str, base_url: str) -> str:
    """清洗用户粘贴的 URL（去空格、末尾斜杠及误粘贴的接口路径后缀）。"""
    norm_provider = (provider or DEFAULT_PROVIDER).strip().lower()
    cleaned = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models", "/api/tags"):
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
    if norm_provider == "ollama":
        if cleaned.lower().endswith("/v1"):
            cleaned = cleaned[:-3].rstrip("/")
        return cleaned or DEFAULT_OLLAMA_BASE_URL
    if norm_provider == "deepseek":
        return cleaned or DEFAULT_DEEPSEEK_BASE_URL
    return cleaned


@dataclass(frozen=True)
class LlmSettings:
    provider: str = DEFAULT_PROVIDER  # "deepseek" | "ollama" | "custom"
    model: str = DEFAULT_MODEL
    base_url: str = ""
    api_key: str | None = None

    @property
    def is_ready(self) -> bool:
        if self.provider == "ollama":
            return bool(self.model.strip())
        return bool(self.api_key and self.model.strip())


def _config_file_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else (Path.home() / ".config")
    return base / "epub-refiner" / "llm_settings.json"


def _read_llm_prefs() -> dict[str, str]:
    path = _config_file_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _write_llm_prefs(provider: str, model: str, base_url: str) -> None:
    path = _config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider.strip() or DEFAULT_PROVIDER,
        "model": model.strip() or DEFAULT_MODEL,
        "base_url": base_url.strip(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_deepseek_api_key() -> str | None:
    """从 Windows 凭据管理器读取 Key；若无则回退到环境变量。"""
    try:
        val = keyring.get_password(SERVICE_NAME, DEEPSEEK_ACCOUNT)
        if val and val.strip():
            return val.strip()
    except KeyringError:
        pass
    env_val = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return env_val or None


def set_deepseek_api_key(api_key: str) -> None:
    cleaned = api_key.strip()
    if not cleaned:
        raise ValueError("API Key 不能为空")
    keyring.set_password(SERVICE_NAME, DEEPSEEK_ACCOUNT, cleaned)


def delete_deepseek_api_key() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, DEEPSEEK_ACCOUNT)
    except KeyringError:
        pass


def get_llm_settings() -> LlmSettings:
    prefs = _read_llm_prefs()
    provider = prefs.get("provider") or os.environ.get("EPUB_REFINER_LLM_PROVIDER", DEFAULT_PROVIDER)
    model = prefs.get("model") or os.environ.get("EPUB_REFINER_LLM_MODEL", DEFAULT_MODEL)
    base_url = prefs.get("base_url") or os.environ.get("EPUB_REFINER_LLM_BASE_URL", "")
    norm_provider = provider.strip() or DEFAULT_PROVIDER
    return LlmSettings(
        provider=norm_provider,
        model=model.strip() or DEFAULT_MODEL,
        base_url=normalize_base_url(norm_provider, base_url),
        api_key=get_deepseek_api_key(),
    )


def save_llm_settings(
    provider: str,
    model: str,
    base_url: str = "",
    api_key: str | None = None,
) -> LlmSettings:
    norm_provider = (provider or DEFAULT_PROVIDER).strip().lower()
    if norm_provider not in {"deepseek", "ollama", "custom"}:
        raise ValueError(f"不支持的模型接入类型: {provider}")

    norm_model = model.strip()
    if not norm_model:
        norm_model = "qwen2.5:7b" if norm_provider == "ollama" else DEFAULT_MODEL

    norm_base = normalize_base_url(norm_provider, base_url)
    if norm_provider == "custom" and not norm_base:
        raise ValueError("自定义接口需要填写服务地址（Base URL）")

    if api_key is not None and api_key.strip():
        set_deepseek_api_key(api_key)
    elif norm_provider != "ollama" and not get_deepseek_api_key():
        raise ValueError("请填写有效的 API Key")

    _write_llm_prefs(norm_provider, norm_model, norm_base)
    return get_llm_settings()


def reset_llm_settings() -> LlmSettings:
    delete_deepseek_api_key()
    path = _config_file_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    return get_llm_settings()


def mask_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}****{api_key[-4:]}"


_RE_SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_RE_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_CN_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_RE_WIN_USER = re.compile(r"(?i)([A-Z]:\\+Users\\+)[^\\\s\"']+")
_RE_UNIX_USER = re.compile(r"(/(?:Users|home)/)[^/\s\"']+")


def redact_sensitive_text(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """对日志、报错信息及外发文本做脱敏处理，屏蔽 API Key、邮箱、手机号与本机用户名路径。"""
    if not text:
        return text
    cleaned = text
    for secret in extra_secrets:
        s = secret.strip()
        if len(s) >= 6 and s in cleaned:
            cleaned = cleaned.replace(s, "[REDACTED_KEY]")
    cleaned = _RE_BEARER.sub("Bearer [REDACTED_TOKEN]", cleaned)
    cleaned = _RE_SK_TOKEN.sub("[REDACTED_API_KEY]", cleaned)
    cleaned = _RE_EMAIL.sub("[REDACTED_EMAIL]", cleaned)
    cleaned = _RE_CN_PHONE.sub("[REDACTED_PHONE]", cleaned)
    cleaned = _RE_WIN_USER.sub(r"\1[USER]", cleaned)
    cleaned = _RE_UNIX_USER.sub(r"\1[USER]", cleaned)
    return cleaned


def _app_prefs_file_path() -> Path:
    return _config_file_path().parent / "app_prefs.json"


def get_default_output_dir() -> Path:
    """默认输出目录：优先在程序所在文件夹内自动创建「精排输出书籍」；若只读则回退到文档目录。"""
    app_dir_env = os.environ.get("EPUB_REFINER_APP_DIR", "").strip()
    base_dir = Path(app_dir_env) if app_dir_env else Path.cwd()
    candidate = base_dir / "精排输出书籍"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback = Path.home() / "Documents" / "EPUB精排输出书籍"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def get_output_dir_settings() -> dict[str, object]:
    default_dir = get_default_output_dir()
    prefs_path = _app_prefs_file_path()
    custom_dir = ""
    if prefs_path.exists():
        try:
            data = json.loads(prefs_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                custom_dir = str(data.get("output_dir") or "").strip()
        except (OSError, json.JSONDecodeError):
            custom_dir = ""

    if custom_dir:
        try:
            custom_path = Path(custom_dir)
            custom_path.mkdir(parents=True, exist_ok=True)
            return {
                "output_dir": str(custom_path),
                "is_custom": True,
                "default_dir": str(default_dir),
            }
        except OSError:
            pass

    return {
        "output_dir": str(default_dir),
        "is_custom": False,
        "default_dir": str(default_dir),
    }


def get_effective_output_dir() -> Path:
    return Path(str(get_output_dir_settings()["output_dir"]))


def save_output_dir(output_dir: str) -> dict[str, object]:
    cleaned = (output_dir or "").strip()
    prefs_path = _app_prefs_file_path()
    prefs_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, str] = {}
    if prefs_path.exists():
        try:
            loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = {str(k): str(v) for k, v in loaded.items()}
        except (OSError, json.JSONDecodeError):
            pass

    if cleaned:
        target = Path(cleaned)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建或写入该文件夹：{exc}") from exc
        data["output_dir"] = str(target)
    else:
        data.pop("output_dir", None)

    prefs_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_output_dir_settings()
