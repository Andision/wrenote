//! Wrenote desktop shell (Tauri).
//!
//! The smallest possible native host for the engine, with the same four
//! responsibilities as the Electron shell it replaces:
//!
//! 1. spawn the engine as a loopback sidecar and hand it a per-launch token;
//! 2. open the main window at the engine's URL once `/health` answers;
//! 3. manage the always-on-top subtitle overlay window (three IPC commands);
//! 4. single instance + clean engine shutdown on exit;
//! 5. open an external URL in the system browser (the update download).
//!
//! Everything else — the UI, the API, permissions to the microphone — is the
//! web client's and the engine's business. The shell knows no API routes.

mod engine;
mod overlay;

use std::process::Child;
use std::sync::Mutex;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

/// Shell-wide state: the running engine and where it listens.
#[derive(Default)]
pub struct Shell {
    engine: Mutex<Option<Child>>,
    base_url: Mutex<Option<String>>,
    overlay_size: Mutex<Option<(f64, f64)>>,
}

impl Shell {
    pub fn base_url(&self) -> Option<String> {
        self.base_url.lock().unwrap().clone()
    }

    fn set_engine(&self, child: Child, base_url: String) {
        *self.engine.lock().unwrap() = Some(child);
        *self.base_url.lock().unwrap() = Some(base_url);
    }

    pub fn stop_engine(&self) {
        if let Some(mut child) = self.engine.lock().unwrap().take() {
            engine::stop(&mut child);
        }
    }

    /// Remembered across overlay opens within a session so reopening keeps the
    /// user's full/compact choice without a resize flash.
    pub fn overlay_size(&self) -> (f64, f64) {
        self.overlay_size.lock().unwrap().unwrap_or(overlay::DEFAULT_SIZE)
    }

    pub fn set_overlay_size(&self, w: f64, h: f64) {
        *self.overlay_size.lock().unwrap() = Some((w, h));
    }
}

/// Start the engine off the UI thread and point the main window at it.
fn boot_engine(app: tauri::AppHandle) {
    std::thread::Builder::new()
        .name("engine-boot".into())
        .spawn(move || {
            let result = engine::command(&app).and_then(|cmd| {
                let token = engine::random_token();
                engine::spawn(cmd, &token)
            });
            match result {
                Ok((child, port)) => {
                    let base = format!("http://{}:{port}", engine::HOST);
                    println!("[wrenote] engine port {port}");
                    if !engine::wait_until_ready(port, engine::READY_TIMEOUT) {
                        eprintln!("[wrenote] engine not ready in time; opening window anyway");
                    }
                    app.state::<Shell>().set_engine(child, base.clone());
                    if let Some(w) = app.get_webview_window(overlay::MAIN) {
                        match base.parse::<tauri::Url>() {
                            Ok(url) => {
                                if let Err(e) = w.navigate(url) {
                                    eprintln!("[wrenote] navigate failed: {e}");
                                }
                            }
                            Err(e) => eprintln!("[wrenote] bad engine url {base}: {e}"),
                        }
                    }
                }
                Err(err) => {
                    eprintln!("[wrenote] could not start engine: {err}");
                    if let Some(w) = app.get_webview_window(overlay::MAIN) {
                        let msg = serde_json::to_string(&format!(
                            "Wrenote could not start its engine.\n{err}"
                        ))
                        .unwrap_or_default();
                        let _ = w.eval(format!(
                            "document.getElementById('msg').innerText = {msg};"
                        ));
                    }
                }
            }
        })
        .expect("engine boot thread");
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(Shell::default())
        // Second launch → focus the existing main window (replaces the engine
        // side's lock file; Electron did the same with requestSingleInstanceLock).
        // `plugin:opener|open_url` — what the web client's openExternal calls.
        // Scoped to http(s) by the capability, so a page can't hand it a file.
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window(overlay::MAIN) {
                if w.is_minimized().unwrap_or(false) {
                    let _ = w.unminimize();
                }
                let _ = w.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            overlay::overlay_toggle,
            overlay::overlay_close,
            overlay::overlay_resize,
        ])
        .setup(|app| {
            // Open immediately on the bundled "Starting…" page so the user sees
            // something during a cold engine start, then navigate to the engine.
            WebviewWindowBuilder::new(app, overlay::MAIN, WebviewUrl::App("index.html".into()))
                .title("Wrenote")
                .inner_size(1280.0, 860.0)
                .build()?;
            boot_engine(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            // The overlay is a companion, not a standalone window.
            if window.label() == overlay::MAIN && matches!(event, WindowEvent::Destroyed) {
                overlay::close(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the Wrenote shell")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                app.state::<Shell>().stop_engine();
            }
        });
}
