// NextGenUp desktop shell: spawns the bundled Python server as a sidecar
// and shows the web UI in a native window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

const SERVER_PORT: u16 = 18790;

struct ServerChild(Mutex<Option<Child>>);

fn server_binary_name() -> &'static str {
    if cfg!(windows) {
        "nextgenup-server.exe"
    } else {
        "nextgenup-server"
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let exe_dir = std::env::current_exe()?
                .parent()
                .expect("executable has a parent dir")
                .to_path_buf();
            let server = exe_dir.join(server_binary_name());

            let data_dir = app
                .path()
                .app_data_dir()
                .expect("app data dir available");
            std::fs::create_dir_all(&data_dir).ok();
            std::fs::create_dir_all(data_dir.join("models")).ok();

            if server.exists() {
                match Command::new(&server)
                    .env("PORT", SERVER_PORT.to_string())
                    .env("NEXTGENUP_DATA", &data_dir)
                    .current_dir(&data_dir)
                    .spawn()
                {
                    Ok(child) => {
                        app.manage(ServerChild(Mutex::new(Some(child))));
                    }
                    Err(e) => eprintln!("failed to start server sidecar: {e}"),
                }
            } else {
                // Dev fallback: assume `python app.py` is running separately.
                eprintln!("server sidecar not found at {server:?}; expecting a dev server");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building NextGenUp")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<ServerChild>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
