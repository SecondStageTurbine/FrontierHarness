#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{collections::HashMap,io::{Read,Write},net::{TcpListener,TcpStream},path::{Path,PathBuf},process::{Child,Command,Stdio},sync::Mutex,time::Duration};
use tauri::{Emitter,Manager,State,WebviewUrl,WebviewWindowBuilder,WindowEvent};
use tauri::menu::{Menu,MenuItem};
use tauri::tray::{TrayIconBuilder,TrayIconEvent};
use portable_pty::{native_pty_system,CommandBuilder,PtySize};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

struct Backend(Mutex<Option<Child>>);
fn stop_backend(child: &mut Child) {
    #[cfg(windows)]
    { let _ = Command::new("taskkill").args(["/PID",&child.id().to_string(),"/T","/F"]).creation_flags(0x08000000).stdout(Stdio::null()).stderr(Stdio::null()).status(); }
    let _ = child.kill();
    let _ = child.wait();
}
impl Drop for Backend {
    fn drop(&mut self) {
        if let Ok(child) = self.0.get_mut() {
            if let Some(mut child) = child.take() { stop_backend(&mut child); }
        }
    }
}

// The user's own terminals, one ConPTY each, in the folder the conversation works in. They run
// with the user's full environment, unlike agent CLIs, because this is the user's shell.
struct Pty { master: Box<dyn portable_pty::MasterPty + Send>, writer: Box<dyn Write + Send>, child: Box<dyn portable_pty::Child + Send + Sync> }
struct Ptys(Mutex<HashMap<String,Pty>>);
#[derive(Clone,serde::Serialize)]
struct PtyData { id: String, data: String }
#[derive(Clone,serde::Serialize)]
struct PtyExit { id: String, code: Option<u32> }

#[tauri::command]
fn pty_open(app: tauri::AppHandle, ptys: State<Ptys>, cwd: String, cols: u16, rows: u16) -> Result<String,String> {
    if !Path::new(&cwd).is_dir() { return Err("That folder is not available.".into()); }
    let pair = native_pty_system().openpty(PtySize{rows,cols,pixel_width:0,pixel_height:0}).map_err(|e|e.to_string())?;
    let mut cmd = if cfg!(windows) { let mut c=CommandBuilder::new("powershell.exe"); c.arg("-NoLogo"); c }
                  else { CommandBuilder::new(std::env::var("SHELL").unwrap_or_else(|_|"/bin/bash".into())) };
    cmd.cwd(&cwd);
    cmd.env("TERM","xterm-256color");
    let child = pair.slave.spawn_command(cmd).map_err(|e|e.to_string())?;
    drop(pair.slave);
    let mut reader = pair.master.try_clone_reader().map_err(|e|e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e|e.to_string())?;
    let id = uuid::Uuid::new_v4().to_string();
    let (emit_id, handle) = (id.clone(), app.clone());
    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => { let _ = handle.emit("pty-data", PtyData{id:emit_id.clone(),data:String::from_utf8_lossy(&buf[..n]).into_owned()}); }
            }
        }
        let code = handle.try_state::<Ptys>().and_then(|s| s.0.lock().ok().and_then(|mut m| m.remove(&emit_id).and_then(|mut p| p.child.wait().ok().map(|st| st.exit_code()))));
        let _ = handle.emit("pty-exit", PtyExit{id:emit_id,code});
    });
    ptys.0.lock().map_err(|_|"terminal registry busy")?.insert(id.clone(), Pty{master:pair.master,writer,child});
    Ok(id)
}
#[tauri::command]
fn pty_write(ptys: State<Ptys>, id: String, data: String) -> Result<(),String> {
    let mut map = ptys.0.lock().map_err(|_|"terminal registry busy")?;
    let pty = map.get_mut(&id).ok_or("This terminal has closed.")?;
    pty.writer.write_all(data.as_bytes()).map_err(|e|e.to_string())
}
#[tauri::command]
fn pty_resize(ptys: State<Ptys>, id: String, cols: u16, rows: u16) -> Result<(),String> {
    let map = ptys.0.lock().map_err(|_|"terminal registry busy")?;
    let pty = map.get(&id).ok_or("This terminal has closed.")?;
    pty.master.resize(PtySize{rows,cols,pixel_width:0,pixel_height:0}).map_err(|e|e.to_string())
}
#[tauri::command]
fn pty_close(ptys: State<Ptys>, id: String) -> Result<(),String> {
    if let Some(mut pty) = ptys.0.lock().map_err(|_|"terminal registry busy")?.remove(&id) { let _ = pty.child.kill(); }
    Ok(())
}
/// Open a project folder in an editor the user has on their PATH, or reveal it in the file manager.
/// The tool is one of a fixed few; nothing typed by the page ever becomes a command name.
#[tauri::command]
fn open_with(tool: String, path: String) -> Result<(),String> {
    if !Path::new(&path).is_dir() { return Err("That folder is not available.".into()); }
    let mut command = match (tool.as_str(), cfg!(windows)) {
        ("code", true) | ("cursor", true) => { let mut c=Command::new("cmd"); c.args(["/C",&tool,&path]); c }
        ("code", false) | ("cursor", false) => { let mut c=Command::new(&tool); c.arg(&path); c }
        ("explorer", true) => { let mut c=Command::new("explorer.exe"); c.arg(&path); c }
        ("explorer", false) => { let mut c=Command::new(if cfg!(target_os="macos") {"open"} else {"xdg-open"}); c.arg(&path); c }
        _ => return Err("Unknown tool.".into()),
    };
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let output = command.stdin(Stdio::null()).output().map_err(|e|e.to_string())?;
    if tool != "explorer" && !output.status.success() {
        let text = String::from_utf8_lossy(&output.stderr).to_string() + &String::from_utf8_lossy(&output.stdout);
        return Err(if text.contains("not recognized") || text.contains("not found") {
            format!("{} is not on your PATH. Install it, or enable its shell command from the editor.", if tool=="code" {"VS Code (code)"} else {"Cursor (cursor)"})
        } else { text.trim().chars().take(300).collect() });
    }
    Ok(())
}

/// Closing the window hides Frontier to the tray unless the user turned that off in Settings.
/// The setting is a flag file the backend writes, read here at each close so no restart is needed.
fn tray_enabled(data: &Path) -> bool {
    std::fs::read_to_string(data.join("tray.json")).ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("enabled").and_then(|e| e.as_bool()))
        .unwrap_or(true)
}
fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}
fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Frontier", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Frontier", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/32x32.png"))?;
    TrayIconBuilder::with_id("main")
        .icon(icon)
        .tooltip("Frontier")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: tauri::tray::MouseButton::Left, .. } = event { show_main(tray.app_handle()); }
        })
        .build(app)?;
    Ok(())
}

/// Keep the machine from sleeping while a turn or an automation runs. A thread pings the system
/// idle timer while the flag is set, which is the pattern that survives thread pool hopping.
static AWAKE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static AWAKE_THREAD: std::sync::OnceLock<()> = std::sync::OnceLock::new();
#[tauri::command]
fn keep_awake(on: bool) {
    AWAKE.store(on, std::sync::atomic::Ordering::Relaxed);
    AWAKE_THREAD.get_or_init(|| {
        std::thread::spawn(|| loop {
            if AWAKE.load(std::sync::atomic::Ordering::Relaxed) {
                #[cfg(windows)]
                unsafe { windows_sys::Win32::System::Power::SetThreadExecutionState(windows_sys::Win32::System::Power::ES_SYSTEM_REQUIRED); }
            }
            std::thread::sleep(Duration::from_secs(30));
        });
    });
}

fn close_ptys(handle: &tauri::AppHandle) {
    if let Some(state)=handle.try_state::<Ptys>() { if let Ok(mut map)=state.0.lock() { for (_,mut pty) in map.drain() { let _=pty.child.kill(); } } }
}

fn launch(app: &mut tauri::App, data: &Path) -> Result<(),Box<dyn std::error::Error>> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf();
    // A stable internal origin preserves WebView drafts and preferences on relaunch,
    // but anything else may hold that port by now, so never make it a launch blocker:
    // fall back to any free port and remember the new one.
    let port_file = data.join("backend.port");
    let preferred = std::fs::read_to_string(&port_file).ok().and_then(|s|s.trim().parse::<u16>().ok()).unwrap_or(0);
    let socket = TcpListener::bind(("127.0.0.1",preferred))
        .or_else(|_|TcpListener::bind(("127.0.0.1",0)))
        .map_err(|_|"Frontier could not open a local port. Check whether security software is blocking it.")?;
    let port = socket.local_addr()?.port();
    std::fs::write(port_file,port.to_string())?;
    drop(socket);
    let ticket = uuid::Uuid::new_v4().to_string() + &uuid::Uuid::new_v4().to_string();
    let packaged = std::env::current_exe()?.parent().unwrap().join("frontier-backend.exe");
    let mut command = if packaged.exists() {
        Command::new(packaged)
    } else {
        if !cfg!(debug_assertions) {
            return Err("The bundled Frontier backend is missing. Reinstall Frontier.".into());
        }
        let python = if cfg!(windows) { root.join(".venv/Scripts/python.exe") } else { root.join(".venv/bin/python") };
        let mut c = Command::new(python);
        c.current_dir(&root).args(["-m", "backend.desktop"]);
        c
    };
    command.env("HARNESS_DATA_DIR", data)
        .env("HARNESS_DESKTOP_TOKEN", &ticket)
        .env("HARNESS_DESKTOP_PORT",port.to_string())
        .env("HARNESS_PARENT_PID",std::process::id().to_string())
        .stdin(Stdio::null()).stdout(Stdio::null())
        .stderr(Stdio::from(std::fs::File::create(data.join("backend.log"))?));
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW: only Frontier's window is visible.
    let mut child = command.spawn()?;
    let address = format!("127.0.0.1:{port}");
    let mut ready = false;
    for _ in 0..450 {
        if TcpStream::connect_timeout(&address.parse()?,Duration::from_millis(100)).is_ok() { ready=true; break; }
        if let Some(status) = child.try_wait()? {
            let tail = std::fs::read_to_string(data.join("backend.log")).ok().and_then(|s| s.lines().rev().find(|l| !l.trim().is_empty()).map(|l| l.to_string())).unwrap_or_default();
            return Err(if tail.contains("already using") { tail } else { format!("Frontier backend exited: {status}. See backend.log in app data.") }.into());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    if !ready { let _=child.kill(); return Err("Frontier's backend did not start in time.".into()); }
    app.manage(Backend(Mutex::new(Some(child))));
    let url = format!("http://127.0.0.1:{port}/desktop/bootstrap?ticket={ticket}");
    WebviewWindowBuilder::new(app,"main",WebviewUrl::External(url.parse()?))
        .title("Frontier")
        .inner_size(1440.0,940.0).min_inner_size(900.0,640.0)
        .center().background_color(tauri::webview::Color(15,15,17,255))
        .on_navigation(move |url| url.host_str()==Some("127.0.0.1") && url.port()==Some(port))
        .build()?;
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        // Registered first: a second launch hands its arguments to this one and exits, instead of
        // starting a second backend that would die on the store lock.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Ptys(Mutex::new(HashMap::new())))
        .invoke_handler(tauri::generate_handler![pty_open,pty_write,pty_resize,pty_close,open_with,keep_awake])
        .setup(|app| {
            let data = app.path().app_local_data_dir()?;
            std::fs::create_dir_all(&data)?;
            // A release build has no console, and no window exists yet, so a panic here would
            // be silent. Leave the reason beside the backend log, and clear it once past.
            let log = data.join("launch-error.log");
            match launch(app,&data) {
                Ok(()) => { let _ = std::fs::remove_file(&log); build_tray(app.handle())?; Ok(()) }
                Err(error) => { let _ = std::fs::write(&log,error.to_string()); Err(error) }
            }
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if let Ok(data) = window.app_handle().path().app_local_data_dir() {
                    if tray_enabled(&data) { api.prevent_close(); let _ = window.hide(); }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Unable to start Frontier");
    app.run(|handle,event| {
        if matches!(event,tauri::RunEvent::Exit) {
            close_ptys(handle);
            if let Some(state)=handle.try_state::<Backend>() {
                if let Ok(mut guard)=state.0.lock() { if let Some(mut child)=guard.take() { stop_backend(&mut child); } }
            }
        }
    });
}
