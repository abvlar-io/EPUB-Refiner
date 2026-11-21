# EPUB 精排工厂 (EPUB Refiner)

一款面向普通读者与电子书爱好者的 **Windows 桌面电子书智能整理工作台**（Tauri v2 + React + Python FastAPI）。无需懂代码，拖入文件即可完成乱码 TXT 还原、自动分章精排、老旧 EPUB 体检修复与批量队列处理。

---

## 核心功能（三大隔离模式，绝不误操作）

1. **① 仅修复乱码 TXT → 正常 TXT**
   - 自动识别 `GB18030` / `GBK` / `Big5` / `UTF-16` / `UTF-8` 等常见文字编码，支持逆向还原“锟斤拷”等双层错位乱码。
   - **严格隔离**：仅另存为纯净 UTF-8 简体中文 `书名_UTF8.txt`，**绝不擅自转换成 EPUB**；导出后可按需手动点击按钮继续精排。
2. **② TXT 一步精排为 EPUB**
   - 自动拆分章节目录、清理常见网站推广与求票广告行，提供 3 套基于**免费开源字体声明**的排版风格（典雅人文 / 现代简约 / 护眼大字）。
3. **③ 残缺 EPUB → 精排修复 EPUB**
   - 自动补全缺失封面、重建空壳目录（`nav.xhtml` / `toc.ncx`）、规范 `text/html` 媒体类型，并清理正文夹带的危险网页脚本（`<script>` / `<iframe>`）。
   - 若本机安装了 Java 与 `EPUBCheck`，可做规范质检并触发 AI 自动修补；未安装 Java 时自动使用内置基础体检与修复。
4. **日常使用体验**
   - **批量处理队列**：支持一次拖入多本书籍排队处理，支持随时追加/移除单本与实时进度条。
   - **自定义保存文件夹**：默认在程序所在目录自动创建 `精排输出书籍` 文件夹，也可随时自定义到桌面或其他磁盘，**原文件绝不被修改**。
   - **系统托盘常驻**：点击右上角关闭按钮可选择“最小化到系统托盘”或“直接退出”，右键托盘菜单支持随时恢复窗口或退出应用。
   - **可选 AI 增强（BYOK）**：不配置也能用内置规则免费完成基础精排；如需更智能的非常规章节识别与坏书页面修补，可接入 **DeepSeek**、**本地 Ollama（离线零费用）** 或自定义 OpenAI 兼容接口（支持填 URL 后自动拉取模型列表下拉选择）。

---

## 下载与使用（支持系统与设备架构）

### 运行环境与硬件要求
| 项目 | 支持范围与说明 |
| :--- | :--- |
| **支持系统** | **Windows 10（1809 及以上）** / **Windows 11** |
| **设备架构** | **x86_64（64 位 PC，Intel / AMD 处理器）**；暂不提供 32 位（x86）、Windows ARM64、macOS 或 Linux 预编译包 |
| **运行依赖** | 安装版与便携版均已内置处理引擎，无需单独安装 Python；界面依赖系统组件 **Microsoft Edge WebView2**（Win11 默认自带，Win10 若缺失安装向导将自动引导补齐） |
| **可选组件** | 若需启用国际标准 `EPUBCheck` 深度质检，需本机另行安装 Java；未安装时会自动降级为内置基础体检与修复，不影响日常排书 |

前往右侧 **Releases** 页面下载任一版本即可：
- **`EPUB精排工厂_电脑安装版.exe`**（或 `.zip`）：标准 Windows 安装向导（支持简体中文 / English 双语安装，自动创建桌面快捷方式）。
- **`EPUB精排工厂_便携版.zip`**：解压即可直接双击 `EPUB精排工厂.exe` 运行，无需安装、可放 U 盘携带（请勿删除同目录下的 `_内部组件` 文件夹）。

> 验证安装包完整性：可核对发布页随附的 `SHA256SUMS.txt`。

---

## 项目目录结构

```text
epub-refiner/
├── desktop/                # Tauri v2 + React + TypeScript 桌面端界面与系统托盘逻辑
│   └── src-tauri/          # Rust 宿主、SVG/ICO 图标资源、NSIS 自定义安装引导
├── engine/                 # Python 3.13 + FastAPI 本地处理引擎（独立打包为 sidecar）
│   ├── src/epub_refiner_engine/
│   │   ├── agent/          # 任务编排器与大模型客户端（含脱敏与隧道重试）
│   │   ├── tools/          # TXT 转码/分章、EPUB 解包重构、EPUBCheck 封装
│   │   ├── secrets_store.py# Windows 凭据管理器加密存储与路径/密钥脱敏
│   │   └── server.py       # 本地 HTTP API 服务
│   └── tests/              # pytest 自动化回归测试
├── scripts/                # 本地环境加载与一键打包脚本（PowerShell 7）
├── LICENSE                 # MIT 开源许可证
└── THIRD_PARTY_NOTICES.txt # 第三方开源组件与字体声明
```

---

## 本地开发与自行打包

环境要求：Windows 10/11 (x64)、PowerShell 7 (`pwsh`)、Python 3.13 + `uv`、Node.js + `npm`、Rust 工具链。

```powershell
# 1. 运行后端静态检查与自动化测试
cd engine
uv run ruff check src tests
uv run pytest -v

# 2. 一键打包安装版与便携版（含 PyInstaller 引擎 + Tauri NSIS）
cd ..
pwsh -File .\scripts\build-release.ps1
```

---

## 商业风险、隐私保护与合法使用警告

1. **合法版权与禁区警告（严禁用于盗版或破解锁）**
   - 本工具仅限用于整理个人合法拥有的**无加密（无 DRM）**电子书；程序会自动拦截带有版权加密锁的文件，**严禁用于破解 DRM 版权保护、批量洗稿或商业盗版分发**。
   - **字体版权合规**：软件内置 CSS 仅声明 `LXGW WenKai`、`Noto Serif/Sans CJK SC`、`Source Han Serif/Sans SC` 等免费开源字体（SIL OFL 1.1）及系统通用族名，**不捆绑、不分发任何商业闭源字体二进制文件**，避免字体商用侵权风险。
2. **第三方大模型与服务协议声明（BYOK 模式）**
   - 本软件为独立开源项目，与 DeepSeek、OpenAI、Ollama 等任何模型或平台厂商均**无官方合作、赞助、授权或从属关系**；不内置、不转售任何共享 API Key。
   - 用户自行接入 DeepSeek 或其他云端接口时，需自行申请密钥、承担相应接口调用费用，并严格遵守对应服务商的开放平台服务协议与内容规范。
3. **数据隐私与敏感信息脱敏**
   - **原文件绝不被修改**：所有输出均另存至独立的输出文件夹，不覆盖或删除原始文件。
   - **绝不上传整本书**：仅在您主动配置云端 AI 时截取少量章节标题样本和出错片段，并在外发前及界面日志中自动对 API Key、Bearer Token、邮箱、手机号与本机用户名路径做脱敏替换；API Key 加密保存在 Windows 系统凭据管理器中。
4. **免责与开源协议**
   - 本软件按“现状（AS IS）”提供，使用前建议对重要电子书原稿自行留存备份；遵循 [MIT License](LICENSE) 开源，第三方依赖及组件许可说明（含 PyInstaller 等组件声明）见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。
