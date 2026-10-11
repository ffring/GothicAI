//! The launcher window and its menu bar icon. Everything the launcher does is in the core (bridge.rs);
//! this side shows windows and gives the page what a web page cannot do: folder dialogs (plugin),
//! Finder, the menu bar.
//!
//! While the game runs the window hides and the launcher lives in the menu bar: its icon opens a small
//! panel (the `tray` window) with the services' state and the journal.

mod bridge;

use std::process::Command;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use serde_json::Value;
use tauri::image::Image;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{ActivationPolicy, AppHandle, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_positioner::{Position, WindowExt};

use bridge::Core;

#[tauri::command]
async fn core_call(
    core: State<'_, Arc<Core>>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    core.call(
        &method,
        params.unwrap_or_else(|| Value::Object(Default::default())),
    )
    .await
}

/// Hides the window: the launcher stays in the menu bar (no Dock icon) while the game runs.
#[tauri::command]
fn to_menu_bar(app: AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.hide();
    }
    let _ = app.set_activation_policy(ActivationPolicy::Accessory);
}

#[tauri::command]
fn show_window(app: AppHandle) {
    show_main(&app);
}

/// Shows a file or folder in Finder.
#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    Command::new("/usr/bin/open")
        .arg("-R")
        .arg(&path)
        .status()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Opens a folder in Finder.
#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    Command::new("/usr/bin/open")
        .arg(&path)
        .status()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn show_main(app: &AppHandle) {
    let _ = app.set_activation_policy(ActivationPolicy::Regular);
    if let Some(tray) = app.get_webview_window("tray") {
        let _ = tray.hide();
    }
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
        let _ = main.unminimize();
        let _ = main.set_focus();
    }
}

fn toggle_panel(app: &AppHandle) {
    let Some(panel) = app.get_webview_window("tray") else {
        return;
    };
    if panel.is_visible().unwrap_or(false) {
        let _ = panel.hide();
    } else {
        let _ = panel.move_window(Position::TrayCenter);
        let _ = panel.show();
        let _ = panel.set_focus();
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app)
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_positioner::init())
        .setup(|app| {
            let core = Core::spawn(app.handle().clone())?;
            app.manage(Arc::new(core));
            TrayIconBuilder::with_id("gothic")
                .icon(Image::from_bytes(include_bytes!("../icons/tray.png"))?)
                .icon_as_template(true)
                .tooltip("Gothic AI")
                .on_tray_icon_event(|tray, event| {
                    tauri_plugin_positioner::on_tray_event(tray.app_handle(), &event);
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle_panel(tray.app_handle());
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| match (window.label(), event) {
            ("tray", WindowEvent::Focused(false)) => {
                let _ = window.hide();
            }
            ("main", WindowEvent::CloseRequested { api, .. }) => {
                let app = window.app_handle();
                if app.state::<Arc<Core>>().running.load(Ordering::SeqCst) {
                    api.prevent_close(); // the game runs: keep going from the menu bar
                    to_menu_bar(app.clone());
                } else {
                    app.exit(0);
                }
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            core_call,
            to_menu_bar,
            show_window,
            reveal,
            open_folder
        ])
        .build(tauri::generate_context!())
        .expect("error while building the launcher");

    app.run(|app, event| match event {
        RunEvent::Reopen { .. } => show_main(app), // a click on the Dock icon
        RunEvent::Exit => app.state::<Arc<Core>>().shutdown(),
        _ => {}
    });
}
