use serde::Serialize;
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent, WindowEvent,
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct EngineConfig {
    base_url: String,
    session_token: String,
}

struct EngineState {
    config: EngineConfig,
    child: Mutex<Option<Child>>,
}

fn pick_free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(47321)
}

fn generate_token() -> String {
    let mut out = String::with_capacity(32);
    for _ in 0..2 {
        let h = RandomState::new().build_hasher().finish();
        out.push_str(&format!("{:016x}", h));
    }
    out
}

fn find_engine_executable() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("_内部组件").join("epub-refiner-engine.exe"));
            candidates.push(dir.join("epub-refiner-engine.exe"));
        }
    }
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(workspace_root) = manifest_dir.parent().and_then(|p| p.parent()) {
        candidates.push(
            workspace_root
                .join("engine")
                .join(".venv")
                .join("Scripts")
                .join("epub-refiner-engine.exe"),
        );
    }
    candidates.into_iter().find(|p| p.exists())
}

fn spawn_engine(port: u16, token: &str) -> Option<Child> {
    let exe = find_engine_executable()?;
    let mut cmd = Command::new(exe);
    cmd.env("EPUB_REFINER_HOST", "127.0.0.1")
        .env("EPUB_REFINER_PORT", port.to_string())
        .env("EPUB_REFINER_SESSION_TOKEN", token)
        .env("EPUB_REFINER_PARENT_PID", std::process::id().to_string());

    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(app_dir) = exe_path.parent() {
            cmd.env("EPUB_REFINER_APP_DIR", app_dir);
        }
    }

    if std::env::var_os("EPUBCHECK_JAR").is_none() {
        let default_jar =
            PathBuf::from(r"E:\EPUB精排项目\tools\epubcheck\epubcheck-5.4.0\epubcheck.jar");
        if default_jar.exists() {
            cmd.env("EPUBCHECK_JAR", default_jar);
        }
    }

    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn().ok()
}

#[tauri::command]
fn get_engine_config(state: tauri::State<'_, EngineState>) -> EngineConfig {
    state.config.clone()
}

#[tauri::command]
fn hide_to_tray(window: tauri::WebviewWindow) {
    let _ = window.hide();
}

#[tauri::command]
fn exit_app(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn reveal_in_explorer(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err("目标文件不存在".to_string());
    }
    #[cfg(windows)]
    {
        if target.is_dir() {
            Command::new("explorer")
                .arg(&target)
                .spawn()
                .map_err(|e| e.to_string())?;
        } else {
            Command::new("explorer")
                .arg(format!("/select,{}", target.display()))
                .spawn()
                .map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = pick_free_port();
    let token = generate_token();
    let config = EngineConfig {
        base_url: format!("http://127.0.0.1:{}", port),
        session_token: token.clone(),
    };
    let child = spawn_engine(port, &token);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState {
            config,
            child: Mutex::new(child),
        })
        .setup(|app| {
            let show_item = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出应用", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

            let mut tray_builder = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("EPUB 精排工厂")
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.unminimize();
                            let _ = win.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.unminimize();
                            let _ = win.set_focus();
                        }
                    }
                });

            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }
            tray_builder.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.emit("app://close-requested", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_engine_config,
            reveal_in_explorer,
            hide_to_tray,
            exit_app
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let state = app_handle.state::<EngineState>();
            if let Ok(mut guard) = state.child.lock() {
                if let Some(mut c) = guard.take() {
                    let _ = c.kill();
                    let _ = c.wait();
                }
            }
        }
    });
}
