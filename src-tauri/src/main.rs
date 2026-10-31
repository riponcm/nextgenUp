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

// How many jobs the local engine is running right now. Any failure (engine
// not up, slow answer) counts as zero so closing the window never hangs.
fn active_jobs() -> u32 {
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration;

    let addr: SocketAddr = ([127, 0, 0, 1], SERVER_PORT).into();
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(500)) {
        Ok(s) => s,
        Err(_) => return 0,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1200)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let req = "GET /api/jobs/active HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(req.as_bytes()).is_err() {
        return 0;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    parse_active(&reply)
}

// Reads the number out of an HTTP reply whose body is {"active": N}.
fn parse_active(reply: &str) -> u32 {
    reply
        .split("\"active\":")
        .nth(1)
        .and_then(|rest| {
            rest.trim_start()
                .split(|c: char| !c.is_ascii_digit())
                .next()
                .map(|n| n.to_string())
        })
        .and_then(|n| n.parse().ok())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::parse_active;

    #[test]
    fn reads_the_job_count() {
        let ok = "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{\"active\":3}\n";
        assert_eq!(parse_active(ok), 3);
        assert_eq!(parse_active("HTTP/1.0 200 OK\r\n\r\n{\"active\": 12}"), 12);
        assert_eq!(parse_active("HTTP/1.0 200 OK\r\n\r\n{\"active\":0}"), 0);
    }

    #[test]
    fn anything_unexpected_counts_as_no_jobs() {
        assert_eq!(parse_active(""), 0);
        assert_eq!(parse_active("HTTP/1.0 403 FORBIDDEN\r\n\r\n{\"error\":\"Not authorized\"}"), 0);
        assert_eq!(parse_active("{\"active\":\"x\"}"), 0);
    }
}

// Keeps the computer from going to sleep while the engine has a job running
// (a long video can take hours; a sleeping machine silently stalls it). The
// screen may still turn off. macOS: `caffeinate -i`, which ends by itself
// when this app exits. Windows: SetThreadExecutionState on this thread.
#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn SetThreadExecutionState(flags: u32) -> u32;
}

fn spawn_keep_awake() {
    std::thread::spawn(|| {
        let mut awake = false;
        #[cfg(target_os = "macos")]
        let mut holder: Option<Child> = None;
        loop {
            std::thread::sleep(std::time::Duration::from_secs(5));
            let busy = active_jobs() > 0;
            if busy == awake {
                continue;
            }
            awake = busy;
            #[cfg(target_os = "macos")]
            {
                if busy {
                    holder = Command::new("caffeinate")
                        .args(["-i", "-w", &std::process::id().to_string()])
                        .spawn()
                        .ok();
                } else if let Some(mut c) = holder.take() {
                    let _ = c.kill();
                    let _ = c.wait();
                }
            }
            #[cfg(windows)]
            unsafe {
                const ES_CONTINUOUS: u32 = 0x8000_0000;
                const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;
                SetThreadExecutionState(if busy {
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                } else {
                    ES_CONTINUOUS
                });
            }
        }
    });
}

// Closing the app stops the engine, which loses a job that is still
// running (a long video can take hours). Ask first. Returns true when the
// close was held back and the question is being shown.
fn hold_close_if_busy(handle: &tauri::AppHandle) -> bool {
    use std::sync::atomic::{AtomicBool, Ordering};
    static ASKING: AtomicBool = AtomicBool::new(false);

    if active_jobs() == 0 {
        return false;
    }
    // A second close while the question is already showing must not stack
    // another dialog; just keep holding the close.
    if ASKING.swap(true, Ordering::SeqCst) {
        return true;
    }
    let handle = handle.clone();
    std::thread::spawn(move || {
        let quit = handle
            .dialog()
            .message(
                "NextGenUp is still working on a job. If you close the app now, \
                 the job will be lost.\n\nClose anyway?",
            )
            .title("Job in progress")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Close anyway".to_string(),
                "Keep working".to_string(),
            ))
            .blocking_show();
        if quit {
            handle.exit(0);
        } else {
            ASKING.store(false, Ordering::SeqCst);
        }
    });
    true
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

        if install {
            // Stop the sidecar BEFORE installing: Windows cannot overwrite a
            // running exe, and on Windows download_and_install exits the
            // process as soon as the installer starts, so cleanup placed
            // after it would never run.
            if let Some(state) = handle.try_state::<ServerChild>() {
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    kill_server_tree(&mut child);
                }
            }
            if update.download_and_install(|_, _| {}, || {}).await.is_ok() {
                handle.restart();
            }
        }
    });
}

fn main() {
    // Linux only (compiled out on macOS and Windows): WebKitGTK's DMA-BUF
    // renderer aborts on some Fedora/NVIDIA setups with "Could not create
    // surfaceless EGL display: EGL_BAD_ALLOC" (issue #8). Must be set before
    // the webview starts; a value the user set themselves is respected.
    #[cfg(target_os = "linux")]
    {
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if hold_close_if_busy(window.app_handle()) {
                    api.prevent_close();
                }
            }
        })
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
                    // The desktop backend must never listen on the network;
                    // LAN serving is a web-mode (run-from-source) feature.
                    .env("NEXTGENUP_HOST", "127.0.0.1")
                    // Lets the engine save results to the user's folder and
                    // clean leftovers; web mode never sets this.
                    .env("NEXTGENUP_DESKTOP", "1")
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
            spawn_keep_awake();
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building NextGenUp")
        .run(|app, event| match event {
            // Cmd+Q / explicit quit while a job runs (closing the window is
            // handled above; an exit the app asked for itself has a code)
            tauri::RunEvent::ExitRequested { code, api, .. } => {
                if code.is_none() && hold_close_if_busy(app) {
                    api.prevent_exit();
                }
            }
            tauri::RunEvent::Exit => {
                if let Some(state) = app.try_state::<ServerChild>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        kill_server_tree(&mut child);
                    }
                }
                // Copies of the user's uploads and extracted video frames are
                // useless once the app is closed; do not leave them behind.
                if let Ok(data_dir) = app.path().app_data_dir() {
                    for sub in ["uploads", "frames"] {
                        let _ = std::fs::remove_dir_all(data_dir.join(sub));
                    }
                }
            }
            _ => {}
        });
}
