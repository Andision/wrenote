//! Floating subtitle overlay ("desktop lyrics").
//!
//! A frameless, transparent, always-on-top window showing the live transcript
//! while recording. It loads the same SPA at `#overlay`; transcript data flows
//! main window → overlay over a same-origin BroadcastChannel, so the shell's
//! only job is window management. The three commands here are what the web
//! client's `window.wrenoteDesktop` bridge calls (see
//! `clients/web/src/lib/desktop.ts`); they have the same names and semantics as
//! the Electron IPC handlers.

use tauri::{AppHandle, LogicalPosition, LogicalSize, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::Shell;

pub const LABEL: &str = "overlay";
pub const MAIN: &str = "main";
/// Initial size = the "full" form; the renderer resizes to "compact" via `overlay_resize`.
pub const DEFAULT_SIZE: (f64, f64) = (760.0, 184.0);
const BOTTOM_MARGIN: f64 = 24.0;

fn is_open(app: &AppHandle) -> bool {
    app.get_webview_window(LABEL).is_some()
}

pub fn close(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(LABEL) {
        let _ = w.close();
    }
}

/// Bring the main window back (the overlay is what the user was watching, so
/// restore the app behind it when it goes away).
fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN) {
        if w.is_minimized().unwrap_or(false) {
            let _ = w.unminimize();
        }
        let _ = w.set_focus();
    }
}

fn open(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<Shell>();
    let Some(base) = state.base_url() else {
        return Err("engine is not running yet".into());
    };
    let (want_w, h) = state.overlay_size();
    let url: tauri::Url = format!("{base}/#overlay").parse().map_err(|e| format!("{e}"))?;

    let mut builder = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::External(url))
        .title("Wrenote subtitles")
        .inner_size(want_w, h)
        .min_inner_size(200.0, 56.0)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(true)
        .minimizable(false)
        .maximizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible_on_all_workspaces(true);

    // Bottom-center of the primary monitor's work area — where subtitles belong.
    if let Ok(Some(m)) = app.primary_monitor() {
        let sf = m.scale_factor();
        let area = m.work_area();
        let (ax, ay) = (area.position.x as f64 / sf, area.position.y as f64 / sf);
        let (aw, ah) = (area.size.width as f64 / sf, area.size.height as f64 / sf);
        let w = want_w.min(aw - 40.0).max(200.0);
        builder = builder
            .inner_size(w, h)
            .position(ax + (aw - w) / 2.0, ay + ah - h - BOTTOM_MARGIN);
    }
    builder.build().map_err(|e| format!("overlay window: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn overlay_toggle(app: AppHandle) -> Result<(), String> {
    if is_open(&app) {
        close(&app);
        show_main(&app);
        return Ok(());
    }
    open(&app)?;
    // Get the app window out of the way — the floating overlay is meant to sit
    // over whatever the user switches to (a meeting, a video, …).
    if let Some(w) = app.get_webview_window(MAIN) {
        let _ = w.minimize();
    }
    Ok(())
}

#[tauri::command]
pub fn overlay_close(app: AppHandle) -> Result<(), String> {
    close(&app);
    show_main(&app);
    Ok(())
}

/// The renderer picks the form (full pill vs compact bar); resize the host
/// window to fit, keeping it pinned at its current bottom-center anchor so it
/// doesn't jump when switching forms.
#[tauri::command]
pub fn overlay_resize(app: AppHandle, width: f64, height: f64) -> Result<(), String> {
    let (width, height) = (width.round().max(200.0), height.round().max(56.0));
    app.state::<Shell>().set_overlay_size(width, height);
    let Some(w) = app.get_webview_window(LABEL) else {
        return Ok(());
    };
    let sf = w.scale_factor().map_err(|e| format!("{e}"))?;
    let pos = w.outer_position().map_err(|e| format!("{e}"))?;
    let size = w.outer_size().map_err(|e| format!("{e}"))?;
    let (x, y) = (pos.x as f64 / sf, pos.y as f64 / sf);
    let (cur_w, cur_h) = (size.width as f64 / sf, size.height as f64 / sf);
    let center_x = x + cur_w / 2.0;
    let bottom_y = y + cur_h;
    w.set_size(LogicalSize::new(width, height)).map_err(|e| format!("{e}"))?;
    w.set_position(LogicalPosition::new(center_x - width / 2.0, bottom_y - height))
        .map_err(|e| format!("{e}"))?;
    Ok(())
}
