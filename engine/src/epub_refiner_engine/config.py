"""引擎运行配置。

所有设置都可以用环境变量覆盖（前缀 EPUB_REFINER_），
桌面外壳（Tauri）启动引擎时会通过环境变量传入端口与会话令牌。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".epub"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EPUB_REFINER_")

    # 安全：只监听本机回环地址，局域网里的其他电脑无法访问
    host: str = "127.0.0.1"
    port: int = 47321

    # 安全：会话令牌。由桌面外壳每次启动时随机生成并传入；
    # 为空表示开发模式（不校验），正式打包后必须由外壳提供。
    session_token: str = ""

    # 外壳进程 PID。引擎会定期检查它是否还活着，外壳退出时引擎自动退出，避免残留后台进程。
    parent_pid: int | None = None

    # 允许跨域访问的界面来源（Tauri 开发服务器与正式包）
    allowed_origins: list[str] = [
        "http://localhost:1420",
        "http://tauri.localhost",
        "tauri://localhost",
    ]


settings = Settings()
