#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{net::{TcpListener,TcpStream},path::PathBuf,process::{Child,Command,Stdio},sync::Mutex,time::Duration};
use tauri::{Manager,WebviewUrl,WebviewWindowBuilder};
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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf();
            let data = app.path().app_local_data_dir()?;
            std::fs::create_dir_all(&data)?;
            // A stable internal origin preserves WebView drafts and preferences on relaunch.
            let port_file = data.join("backend.port");
            let preferred = std::fs::read_to_string(&port_file).ok().and_then(|s|s.trim().parse::<u16>().ok()).unwrap_or(0);
            let socket = TcpListener::bind(("127.0.0.1",preferred)).map_err(|_|"Frontier's internal port is busy. Close the other Frontier window before launching again.")?;
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
            command.env("HARNESS_DATA_DIR", &data)
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
                if let Some(status) = child.try_wait()? { return Err(format!("Frontier backend exited: {status}. See backend.log in app data.").into()); }
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
        })
        .build(tauri::generate_context!())
        .expect("Unable to start Frontier");
    app.run(|handle,event| {
        if matches!(event,tauri::RunEvent::Exit) {
            if let Some(state)=handle.try_state::<Backend>() {
                if let Ok(mut guard)=state.0.lock() { if let Some(mut child)=guard.take() { stop_backend(&mut child); } }
            }
        }
    });
}
