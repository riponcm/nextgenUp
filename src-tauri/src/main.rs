// NextGenUp desktop shell: spawns the bundled Python server as a sidecar
// and shows the web UI in a native window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::UpdaterExt;

const SERVER_PORT: u16 = 18790;

struct ServerChild(Mutex<Option<Child>>);

fn server_binary_name() -> &'static str {
    if cfg!(windows) {
        "nextgenup-server.exe"
    } else {
        "nextgenup-server"
    }
}

// PyInstaller one-file binaries run as a bootloader that spawns the real
// server as its own child, so killing only the direct child leaves an
// orphan. Kill the whole tree: the process group on Unix, taskkill /T on
// Windows.
fn kill_server_tree(child: &mut Child) {
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .output();
    }
    let _ = child.kill();
    let _ = child.wait();
}

// Check GitHub releases for a newer signed build; ask, install, restart.
fn spawn_update_check(handle: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        let updater = match handle.updater() {
            Ok(u) => u,
            Err(_) => return,
        };
        let update = match updater.check().await {
            Ok(Some(u)) => u,
            _ => return,
        };

        let msg = format!(
            "NextGenUp {} is available (you have {}).\n\nDownload and install now?",
            update.version, update.current_version
        );
        let install = handle
            .dialog()
            .message(msg)
            .title("Update available")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Install".to_string(),
                "Later".to_string(),
            ))
            .blocking_show();

        if install && update.download_and_install(|_, _| {}, || {}).await.is_ok() {
            // Stop the sidecar before the app restarts into the new version
            if let Some(state) = handle.try_state::<ServerChild>() {
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    kill_server_tree(&mut child);
                }
            }
            handle.restart();
        }
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
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
                let mut cmd = Command::new(&server);
                cmd.env("PORT", SERVER_PORT.to_string())
                    .env("NEXTGENUP_DATA", &data_dir)
                    .current_dir(&data_dir);
                #[cfg(unix)]
                {
                    use std::os::unix::process::CommandExt;
                    cmd.process_group(0);
                }
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                    cmd.creation_flags(CREATE_NO_WINDOW);
                }
                match cmd.spawn() {
                    Ok(child) => {
                        app.manage(ServerChild(Mutex::new(Some(child))));
                    }
                    Err(e) => eprintln!("failed to start server sidecar: {e}"),
                }
            } else {
                // Dev fallback: assume `python app.py` is running separately.
                eprintln!("server sidecar not found at {server:?}; expecting a dev server");
            }

            spawn_update_check(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building NextGenUp")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<ServerChild>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        kill_server_tree(&mut child);
                    }
                }
            }
        });
}
