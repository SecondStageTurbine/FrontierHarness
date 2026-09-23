fn main() {
    // The UI is served from the loopback backend, a remote origin to Tauri, so every command the
    // page may call is named here and granted in capabilities/desktop.json.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["pty_open", "pty_write", "pty_resize", "pty_close", "open_with", "keep_awake"]),
    ))
    .expect("failed to run tauri-build");
}
