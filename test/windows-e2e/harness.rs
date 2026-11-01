// One binary, three roles picked by its file name, so the Windows runner
// starts processes exactly as Herdr does:
//   app.exe <program> <args…>  herdr-app mods.rs run_blocking + spawn::command
//   core.exe <via> <line>      herdr-core machines.rs Reach::command for a via
//   docker.exe <args…>         fake_docker.py beside it
use std::env;
use std::io::Write;
use std::os::windows::process::CommandExt;
use std::process::{exit, Command, Stdio};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn main() {
    let exe = env::current_exe().expect("own path");
    let role = exe.file_stem().expect("name").to_string_lossy().to_lowercase();
    let args: Vec<String> = env::args().skip(1).collect();
    let status = match role.as_str() {
        "app" => {
            let out = Command::new(&args[0])
                .args(&args[1..])
                .creation_flags(CREATE_NO_WINDOW)
                .stdin(Stdio::null())
                .output()
                .unwrap_or_else(|e| {
                    eprintln!("cannot run {}: {e}", args[0]);
                    exit(126)
                });
            std::io::stdout().write_all(&out.stdout).unwrap();
            std::io::stderr().write_all(&out.stderr).unwrap();
            out.status
        }
        "core" => Command::new("sh")
            .arg("-c")
            .arg(format!("{} \"$1\"", args[0]))
            .arg("sh")
            .arg(&args[1])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .expect("sh"),
        _ => Command::new("python")
            .arg(exe.with_file_name("fake_docker.py"))
            .args(&args)
            .status()
            .expect("python"),
    };
    exit(status.code().unwrap_or(1));
}
