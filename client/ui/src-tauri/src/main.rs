#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::sync::Mutex;
use std::io::Write;
use serde::Serialize;
use tauri::{Manager, State};
use tauri_plugin_shell::{ShellExt, process::{CommandChild, CommandEvent}};

#[derive(Clone, Serialize)]
struct Session { endpoint: String, token: String }
#[derive(Default)]
struct EngineState { session: Mutex<Option<Session>>, error: Mutex<Option<String>>, child: Mutex<Option<CommandChild>> }

#[tauri::command]
fn local_session(state: State<EngineState>) -> Result<Option<Session>, String> {
    if let Some(error) = state.error.lock().map_err(|e|e.to_string())?.clone() { return Err(error); }
    Ok(state.session.lock().map_err(|e|e.to_string())?.clone())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![local_session])
        .setup(|app| {
            let data = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data)?;
            let mut diagnostic = std::fs::File::create(data.join("engine.log"))?;
            let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
            #[cfg(feature = "bundled-engine-dir")]
            let engine_command = app.shell().command(app.path().resolve("engine/books-engine.exe", tauri::path::BaseDirectory::Resource)?);
            #[cfg(not(feature = "bundled-engine-dir"))]
            let engine_command = app.shell().sidecar("books-engine")?;
            let mut command = engine_command
                .env("BOOKS_LOCAL_TOKEN", &token).env("BOOKS_DATA", data.to_string_lossy().as_ref());
            if let Some(url) = option_env!("BOOKS_LICENSE_URL") { command = command.env("BOOKS_LICENSE_URL", url); }
            if let Some(key) = option_env!("BOOKS_LICENSE_PUBLIC_KEY") { command = command.env("BOOKS_LICENSE_PUBLIC_KEY", key); }
            let (mut rx, child) = command.spawn()?;
            *app.state::<EngineState>().child.lock().unwrap() = Some(child);
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            if let Ok(value) = serde_json::from_slice::<serde_json::Value>(&bytes) {
                                if value["type"] == "ready" {
                                    if let Some(port) = value["port"].as_u64().filter(|p| *p > 0 && *p <= 65535) {
                                        *handle.state::<EngineState>().session.lock().unwrap() = Some(Session { endpoint: format!("http://127.0.0.1:{}", port), token: token.clone() });
                                    }
                                }
                            }
                        },
                        CommandEvent::Stderr(bytes) => {
                            let _ = diagnostic.write_all(&bytes);
                            let _ = diagnostic.write_all(b"\n");
                        },
                        CommandEvent::Error(message) => { *handle.state::<EngineState>().error.lock().unwrap() = Some(message); },
                        CommandEvent::Terminated(_) => {
                            *handle.state::<EngineState>().session.lock().unwrap() = None;
                            *handle.state::<EngineState>().error.lock().unwrap() = Some("Обработчик остановился. Перезапустите приложение".into());
                        },
                        _ => {}
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!()).expect("Не удалось открыть приложение");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(child) = handle.state::<EngineState>().child.lock().unwrap().take() { let _ = child.kill(); }
        }
    });
}
