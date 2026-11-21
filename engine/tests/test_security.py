from epub_refiner_engine.agent.llm_client import _resolve_model_call_kwargs
from epub_refiner_engine.secrets_store import (
    LlmSettings,
    mask_api_key,
    normalize_base_url,
    redact_sensitive_text,
)


def test_mask_api_key_never_exposes_secret() -> None:
    secret = "sk-1234567890abcdef"
    masked = mask_api_key(secret)
    assert secret not in masked
    assert masked.startswith("sk-")
    assert masked.endswith("cdef")
    assert "****" in masked


def test_redact_sensitive_text_removes_keys_emails_and_phones() -> None:
    raw = (
        "Error with key sk-test1234567890abcd and Bearer tok_abcdefgh123456; "
        r"contact user@example.com or 13800138000 at C:\Users\alice\AppData\Local\Temp."
    )
    redacted = redact_sensitive_text(raw, ("sk-test1234567890abcd",))
    assert "sk-test1234567890abcd" not in redacted
    assert "tok_abcdefgh123456" not in redacted
    assert "user@example.com" not in redacted
    assert "13800138000" not in redacted
    assert "alice" not in redacted
    assert "[REDACTED_KEY]" in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted


def test_normalize_base_url_strips_accidental_suffixes() -> None:
    assert normalize_base_url("deepseek", "https://api.deepseek.com/v1/chat/completions/") == "https://api.deepseek.com/v1"
    assert normalize_base_url("deepseek", "") == "https://api.deepseek.com"
    assert normalize_base_url("ollama", "http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"


def test_resolve_model_call_kwargs_normalizes_prefixes() -> None:
    kw_ds = _resolve_model_call_kwargs(
        LlmSettings(provider="deepseek", model="deepseek-v4-pro", base_url="https://api.deepseek.com", api_key="sk-1")
    )
    assert kw_ds["model"] == "openai/deepseek-v4-pro"
    assert kw_ds["api_base"] == "https://api.deepseek.com/v1"

    kw_custom = _resolve_model_call_kwargs(
        LlmSettings(provider="custom", model="Qwen/Qwen2.5-72B-Instruct", base_url="https://api.siliconflow.cn/v1", api_key="sk-2")
    )
    assert kw_custom["model"] == "openai/Qwen/Qwen2.5-72B-Instruct"
    assert kw_custom["api_base"] == "https://api.siliconflow.cn/v1"
