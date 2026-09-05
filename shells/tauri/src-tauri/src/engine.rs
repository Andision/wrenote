//! Engine sidecar: spawn `wrenote-server`, learn its port, wait for `/health`.
//!
//! Mirrors `startServer` / `waitUntilReady` in the Electron shell. The engine
//! binds an OS-assigned loopback port itself and prints exactly one
//! `WRENOTE_PORT=<n>` line on stdout; the per-launch auth token goes in via the
//! `WRENOTE_AUTH_TOKEN` environment variable and the engine cookies it onto the
//! web client when the page loads, so nothing shell-specific reaches the SPA.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use tauri::Manager;

pub const HOST: &str = "127.0.0.1";
/// How long we give the engine to print its port and answer `/health`.
/// Cold starts on a slow disk (PyInstaller onedir + Python imports) can take a while.
pub const READY_TIMEOUT: Duration = Duration::from_secs(30);

/// How to launch the engine on this machine.
pub struct EngineCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
}

/// Packaged: the PyInstaller-frozen `wrenote-server` under the app's
/// resources (see `bundle.resources` in tauri.conf.json). Dev: `python -m
/// wrenote.run_server` from the repo's `engine/` dir, using `$WRENOTE_PYTHON`
/// when set (e.g. a conda env) and otherwise whatever `python3`/`python` is
/// on PATH.
pub fn command(app: &tauri::AppHandle) -> Result<EngineCommand, String> {
    if !tauri::is_dev() {
        let dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("resource dir: {e}"))?
            .join("wrenote-server");
        let exe = dir.join(if cfg!(windows) { "wrenote-server.exe" } else { "wrenote-server" });
        return Ok(EngineCommand { program: exe, args: vec![], cwd: Some(dir) });
    }
    let engine_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join("engine");
    let python = std::env::var("WRENOTE_PYTHON")
        .unwrap_or_else(|_| if cfg!(windows) { "python" } else { "python3" }.to_string());
    Ok(EngineCommand {
        program: PathBuf::from(python),
        args: vec!["-m".into(), "wrenote.run_server".into()],
        cwd: Some(engine_dir),
    })
}

/// A random per-launch loopback token (hex; the engine only compares strings).
pub fn random_token() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Spawn the engine and block until it reports its port (or dies / times out).
pub fn spawn(cmd: EngineCommand, token: &str) -> Result<(Child, u16), String> {
    let mut c = Command::new(&cmd.program);
    c.args(&cmd.args)
        .env("WRENOTE_AUTH_TOKEN", token)
        .stdin(Stdio::null())
        .stdout(Stdio::piped()) // parsed for the port, then echoed
        .stderr(Stdio::inherit()); // uvicorn logs
    if let Some(cwd) = &cmd.cwd {
        c.current_dir(cwd);
    }
    #[cfg(windows)]
    {
        // The frozen engine is a console-subsystem exe (it must keep a real
        // stdout for the port handshake); without this Windows pops a terminal
        // window for it. Same as Electron's `windowsHide: true`.
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        c.creation_flags(CREATE_NO_WINDOW);
    }
    println!(
        "[wrenote] spawning: {} {} (cwd={:?})",
        cmd.program.display(),
        cmd.args.join(" "),
        cmd.cwd
    );
    let mut child = c
        .spawn()
        .map_err(|e| format!("could not start engine {}: {e}", cmd.program.display()))?;
    let stdout = child.stdout.take().ok_or("engine stdout not captured")?;

    let (tx, rx) = mpsc::channel::<u16>();
    std::thread::Builder::new()
        .name("engine-stdout".into())
        .spawn(move || {
            let mut sent = false;
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                println!("[engine] {line}");
                if !sent {
                    if let Some(port) = line
                        .trim()
                        .strip_prefix("WRENOTE_PORT=")
                        .and_then(|p| p.trim().parse::<u16>().ok())
                    {
                        let _ = tx.send(port);
                        sent = true;
                    }
                }
            }
        })
        .map_err(|e| format!("stdout reader thread: {e}"))?;

    // The sender is dropped when the reader thread ends, i.e. when the engine
    // exits — so a crash before the handshake fails fast instead of waiting
    // out the whole timeout.
    match rx.recv_timeout(READY_TIMEOUT) {
        Ok(port) => Ok((child, port)),
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            Err("engine exited or timed out before reporting a port".into())
        }
    }
}

/// Poll `GET /health` (public, no token) until it answers 200.
pub fn wait_until_ready(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if probe_health(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

fn probe_health(port: u16) -> bool {
    let Some(addr) = (HOST, port).to_socket_addrs().ok().and_then(|mut a| a.next()) else {
        return false;
    };
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_secs(1)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(1)));
    let req = format!("GET /health HTTP/1.1\r\nHost: {HOST}:{port}\r\nConnection: close\r\n\r\n");
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 64];
    let n = s.read(&mut buf).unwrap_or(0);
    let head = &buf[..n];
    head.starts_with(b"HTTP/1.1 200") || head.starts_with(b"HTTP/1.0 200")
}

/// Ask the engine to shut down (SIGTERM so uvicorn closes the store cleanly),
/// then make sure it is gone.
pub fn stop(child: &mut Child) {
    println!("[wrenote] terminating engine");
    #[cfg(unix)]
    {
        // SAFETY: plain syscall on a pid we own; a stale pid just returns ESRCH.
        unsafe {
            libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}
