//! Reference input field for Windows, (b) direct TSF capture: records this thread's own TSF
//! composition and candidate list as content-free `gt/1` lines (`src: "win_tsf"`).
//!
//! Usage (the result is reported only through the exit code; nothing is written to the
//! console):
//!   refinput_tsf --out <path>              Creates one window (a RichEdit control) and records
//!                                           until it is closed (an existing <path> is never
//!                                           overwritten).
//!   refinput_tsf --selftest [--out <path>] Does not activate TSF; writes a representative
//!                                           sequence through the core and the state machine
//!                                           and reads it back for validation.
//!
//! Exit code: 0 success / 1 a runtime failure (window, TSF, write, or self-test mismatch) / 2
//! usage error, or not running on Windows.
//!
//! No text is ever captured (a length is only `ITfRangeACP::GetExtent`'s `cch`). TSF belonging
//! to another process is never observed. The capture strategy is described in `tsf.rs` and in
//! the README. Notifications are recorded as received, in the order received, with no
//! interpretation.

use std::process::ExitCode;

#[cfg(windows)]
#[path = "../tsf.rs"]
mod tsf;

#[cfg(windows)]
fn main() -> ExitCode {
    imp::run()
}

#[cfg(not(windows))]
fn main() -> ExitCode {
    // The reference input field's window runs only on Windows; this binary keeps an empty
    // entry point on other platforms so that `cargo test` still passes there.
    ExitCode::from(2)
}

#[cfg(windows)]
mod imp {
    use std::cell::{Cell, RefCell};
    use std::ffi::OsString;
    use std::fs::{File, OpenOptions};
    use std::io::{Read, Write};
    use std::path::{Path, PathBuf};
    use std::process::ExitCode;
    use std::rc::Rc;
    use std::time::{SystemTime, UNIX_EPOCH};

    use refinput_windows::{check_line, header, line, ticks_to_ns, Ev, Src, UiKind};
    use windows::core::{w, PCWSTR};
    use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
    use windows::Win32::Graphics::Gdi::{GetSysColorBrush, UpdateWindow, COLOR_WINDOW};
    use windows::Win32::System::LibraryLoader::{
        GetModuleHandleW, LoadLibraryExW, LOAD_LIBRARY_SEARCH_SYSTEM32,
    };
    use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
    use windows::Win32::UI::Controls::RichEdit::{EM_SETEDITSTYLE, MSFTEDIT_CLASS, SES_USECTF};
    use windows::Win32::UI::Input::KeyboardAndMouse::SetFocus;
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetMessageW, GetWindow,
        LoadCursorW, PostQuitMessage, RegisterClassExW, SendMessageW, ShowWindow, TranslateMessage,
        CS_HREDRAW, CS_VREDRAW, CW_USEDEFAULT, ES_AUTOVSCROLL, ES_MULTILINE, GW_CHILD, IDC_ARROW,
        MSG, SW_SHOW, WINDOW_STYLE, WM_DESTROY, WM_KEYDOWN, WM_SETFOCUS, WNDCLASSEXW, WS_CAPTION,
        WS_CHILD, WS_EX_CLIENTEDGE, WS_OVERLAPPED, WS_SYSMENU, WS_VISIBLE, WS_VSCROLL,
    };

    use super::tsf::{self, CompTracker};

    const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

    // ---- recording (clock and output) ------------------------------------------------------

    /// The output file and the monotonic clock's frequency.
    struct Out {
        file: File,
        qpc_freq: i64,
    }

    fn qpc_freq() -> i64 {
        let mut f = 0i64;
        // SAFETY: the output pointer is a valid i64 location.
        match unsafe { QueryPerformanceFrequency(&mut f) } {
            Ok(()) => f,
            Err(_) => 0,
        }
    }

    /// The monotonic clock (`QueryPerformanceCounter`), converted to nanoseconds.
    fn mono_ns(freq: i64) -> u64 {
        let mut t = 0i64;
        // SAFETY: the output pointer is a valid i64 location.
        match unsafe { QueryPerformanceCounter(&mut t) } {
            Ok(()) => ticks_to_ns(t, freq),
            Err(_) => 0,
        }
    }

    /// The wall clock, in Unix milliseconds.
    fn wall_ms() -> u64 {
        match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(d) => u64::try_from(d.as_millis()).unwrap_or(u64::MAX),
            Err(_) => 0,
        }
    }

    fn append(file: &mut File, text: &str) -> std::io::Result<()> {
        // Each line is written with a trailing LF, with no byte-order mark and no CR, and is
        // handed to the operating system as soon as it is written, so that it survives even if
        // the process is later terminated.
        let mut buf = String::with_capacity(text.len() + 1);
        buf.push_str(text);
        buf.push('\n');
        file.write_all(buf.as_bytes())
    }

    /// Builds a sink that records one event, together with the time at which it arrived. If a
    /// write fails, it raises a flag and stops the message loop, since a sink cannot itself
    /// return a failure; the flag is later turned into the exit code.
    fn make_recorder(file: File, failed: Rc<Cell<bool>>) -> tsf::Recorder {
        let out = RefCell::new(Out {
            file,
            qpc_freq: qpc_freq(),
        });
        Rc::new(move |ev: Ev| {
            let Ok(mut o) = out.try_borrow_mut() else {
                // A reentrant call while an event is already being written would otherwise be
                // silently dropped; treat it as a recording failure instead of losing the event.
                failed.set(true);
                // SAFETY: a simple call with no arguments, made on this window's own thread.
                unsafe { PostQuitMessage(1) };
                return;
            };
            let text = line(ev, wall_ms(), mono_ns(o.qpc_freq));
            if append(&mut o.file, &text).is_err() {
                failed.set(true);
                // SAFETY: a simple call with no arguments, made on this window's own thread.
                unsafe { PostQuitMessage(1) };
            }
        })
    }

    // ---- window --------------------------------------------------------------------------------

    /// The parent window: passes focus on to the RichEdit control (its child) and stops the
    /// loop once closed.
    unsafe extern "system" fn wnd_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match msg {
            WM_SETFOCUS => {
                // SAFETY: retrieves this window's own child window (the RichEdit control) and
                // passes focus to it.
                if let Ok(edit) = unsafe { GetWindow(hwnd, GW_CHILD) } {
                    let _ = unsafe { SetFocus(edit) };
                }
                return LRESULT(0);
            }
            WM_DESTROY => {
                // SAFETY: a simple call with no arguments.
                unsafe { PostQuitMessage(0) };
                return LRESULT(0);
            }
            _ => {}
        }
        // SAFETY: passes the received arguments through, unchanged, to the default handler.
        unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
    }

    /// Creates the parent window and the RichEdit control, activates TSF, pumps messages, and
    /// deactivates TSF once the window is closed.
    fn run_window(rec: tsf::Recorder) -> windows::core::Result<i32> {
        // The sinks are registered before the window (RichEdit control) is created, so that no
        // focus notification is missed.
        let tsf = tsf::Tsf::start(rec.clone())?;
        // SAFETY: Win32 window creation and the message loop, entirely self-contained within
        // this function (and this thread).
        let result = unsafe {
            (|| -> windows::core::Result<i32> {
                let hinstance: HINSTANCE = GetModuleHandleW(PCWSTR::null())?.into();
                // Load only from the system directory, not the working directory or the
                // executable's own folder, so a same-named file placed there cannot be loaded
                // instead of the intended system library.
                LoadLibraryExW(w!("Msftedit.dll"), None, LOAD_LIBRARY_SEARCH_SYSTEM32)?;
                let class_name = w!("RefInputTsf");
                let class = WNDCLASSEXW {
                    cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
                    style: CS_HREDRAW | CS_VREDRAW,
                    lpfnWndProc: Some(wnd_proc),
                    hInstance: hinstance,
                    hCursor: LoadCursorW(None, IDC_ARROW)?,
                    hbrBackground: GetSysColorBrush(COLOR_WINDOW),
                    lpszClassName: class_name,
                    ..Default::default()
                };
                if RegisterClassExW(&class) == 0 {
                    return Err(windows::core::Error::from_win32());
                }
                let hwnd = CreateWindowExW(
                    Default::default(),
                    class_name,
                    w!("refinput_tsf (gt/1 win_tsf)"),
                    WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
                    CW_USEDEFAULT,
                    CW_USEDEFAULT,
                    640,
                    240,
                    None,
                    None,
                    hinstance,
                    None,
                )?;
                // Hosts a TSF-aware control (RichEdit); the TSF document belongs to this
                // control.
                let edit = match CreateWindowExW(
                    WS_EX_CLIENTEDGE,
                    MSFTEDIT_CLASS,
                    PCWSTR::null(),
                    WS_CHILD
                        | WS_VISIBLE
                        | WS_VSCROLL
                        | WINDOW_STYLE((ES_MULTILINE | ES_AUTOVSCROLL) as u32),
                    10,
                    10,
                    600,
                    150,
                    hwnd,
                    None,
                    hinstance,
                    None,
                ) {
                    Ok(edit) => edit,
                    Err(err) => {
                        // Clean up the parent window created above instead of leaking it.
                        // SAFETY: `hwnd` was just created by this function and is not used
                        // again on this path.
                        let _ = DestroyWindow(hwnd);
                        return Err(err);
                    }
                };
                // Requests TSF use explicitly, although it is already the default.
                SendMessageW(
                    edit,
                    EM_SETEDITSTYLE,
                    WPARAM(SES_USECTF as usize),
                    LPARAM(SES_USECTF as isize),
                );
                let _ = ShowWindow(hwnd, SW_SHOW);
                let _ = UpdateWindow(hwnd);
                let _ = SetFocus(edit);

                let mut msg = MSG::default();
                loop {
                    let got = GetMessageW(&mut msg, None, 0, 0);
                    if got.0 <= 0 {
                        // 0 = WM_QUIT, -1 = an error
                        return if got.0 == 0 {
                            Ok(msg.wParam.0 as i32)
                        } else {
                            Err(windows::core::Error::from_win32())
                        };
                    }
                    // Only the kind of key (`vk`) is recorded, before it is passed on to the
                    // control; no character is captured.
                    if msg.message == WM_KEYDOWN {
                        rec(Ev::KeyDown {
                            vk: msg.wParam.0 as u32,
                        });
                    }
                    let _ = TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
            })()
        };
        tsf.stop();
        result
    }

    // ---- arguments and self-test -------------------------------------------------------------

    struct Args {
        selftest: bool,
        out: Option<PathBuf>,
    }

    /// Parses `--out <path>` / `--selftest`. An unknown argument, a missing value, or a
    /// duplicate flag is a usage error.
    fn parse_args<I: Iterator<Item = OsString>>(mut it: I) -> Result<Args, ()> {
        let mut args = Args {
            selftest: false,
            out: None,
        };
        while let Some(a) = it.next() {
            match a.to_str() {
                Some("--selftest") if !args.selftest => args.selftest = true,
                Some("--out") if args.out.is_none() => match it.next() {
                    Some(p) if !p.is_empty() => args.out = Some(PathBuf::from(p)),
                    _ => return Err(()),
                },
                _ => return Err(()),
            }
        }
        Ok(args)
    }

    /// The representative sequence the self-test writes. TSF is not activated; instead,
    /// observations are fed into the state machine (`CompTracker`) to obtain events: one
    /// conversion (start, update, candidate window, commit), followed by one cancellation.
    /// Timestamps are fixed values.
    fn selftest_events() -> Vec<Ev> {
        let cand = UiKind::Candidate;
        let mut t = CompTracker::default();
        let mut evs = vec![Ev::FocusIn, Ev::KeyDown { vk: 0xE5 }];
        evs.extend(t.observe(Some((1, 0, 1)), Some(1)));
        evs.extend(t.observe(Some((2, 0, 1)), Some(2)));
        evs.push(Ev::UiBegin { kind: cand });
        evs.push(Ev::UiUpdate { kind: cand });
        evs.extend(t.observe(Some((2, 0, 1)), Some(2)));
        evs.push(Ev::UiEnd { kind: cand });
        // Commit: the composition disappears, and its text stays in the document (document end
        // remains 2).
        evs.extend(t.observe(None, Some(2)));
        evs.push(Ev::KeyDown { vk: 0xE5 });
        evs.extend(t.observe(Some((3, 2, 1)), Some(5)));
        // Cancel: the composition's text also disappears from the document (5 -> 2).
        evs.extend(t.observe(None, Some(2)));
        evs.push(Ev::UiBegin {
            kind: UiKind::Reading,
        });
        evs.push(Ev::UiEnd {
            kind: UiKind::Reading,
        });
        evs.push(Ev::UiBegin {
            kind: UiKind::Other,
        });
        evs.push(Ev::UiEnd {
            kind: UiKind::Other,
        });
        evs.push(Ev::FocusOut);
        evs
    }

    /// Writes the sequence through the core and reads it back for validation. Passing this
    /// check does not by itself establish that a real input method engine would deliver the
    /// same notifications; that is confirmed separately, in a calibration pilot.
    fn selftest(path: &Path) -> bool {
        let events = selftest_events();
        // The state machine distinguishes commit from cancellation (two `tsf_end` events, with
        // lengths 2 and 0).
        let ends: Vec<Option<u32>> = events
            .iter()
            .filter_map(|e| match e {
                Ev::TsfEnd { result_len } => Some(*result_len),
                _ => None,
            })
            .collect();
        if ends != vec![Some(2), Some(0)] {
            return false;
        }
        let mut lines = vec![header(Src::WinTsf, APP_VERSION)];
        for (i, ev) in events.into_iter().enumerate() {
            let i = i as u64;
            lines.push(line(
                ev,
                1_700_000_000_000 + i * 10,
                1_000_000_000 + i * 10_000_000,
            ));
        }
        // As with a raw log, the self-test never overwrites an existing output path either.
        let written = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .and_then(|mut f| {
                for l in &lines {
                    append(&mut f, l)?;
                }
                f.flush()
            });
        if written.is_err() {
            return false;
        }
        let mut text = String::new();
        if File::open(path)
            .and_then(|mut f| f.read_to_string(&mut text))
            .is_err()
        {
            return false;
        }
        // Reads back exactly as written: no byte-order mark, no CR, nothing missing.
        let mut expected = lines.join("\n");
        expected.push('\n');
        if text != expected {
            return false;
        }
        // Every line has the content-free gt/1 shape (a header on the first line only; an
        // event on every line after it).
        let mut it = text.lines();
        let head_ok = it.next().is_some_and(|h| {
            h.contains("\"kind\":\"header\"")
                && h.contains("\"src\":\"win_tsf\"")
                && check_line(h).is_ok()
        });
        head_ok && it.all(|l| l.contains("\"kind\":\"ev\"") && check_line(l).is_ok())
    }

    pub fn run() -> ExitCode {
        let Ok(args) = parse_args(std::env::args_os().skip(1)) else {
            return ExitCode::from(2);
        };
        if args.selftest {
            // With no `--out`, write to a temporary file and remove it afterward.
            let (path, temp) = match args.out {
                Some(p) => (p, false),
                None => (
                    std::env::temp_dir().join(format!(
                        "refinput_tsf_selftest_{}.jsonl",
                        std::process::id()
                    )),
                    true,
                ),
            };
            let ok = selftest(&path);
            if temp {
                let _ = std::fs::remove_file(&path);
            }
            return if ok {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(1)
            };
        }
        let Some(path) = args.out else {
            return ExitCode::from(2);
        };
        // A raw log is never overwritten: fail if the path already exists.
        let Ok(mut file) = OpenOptions::new().write(true).create_new(true).open(&path) else {
            return ExitCode::from(1);
        };
        if append(&mut file, &header(Src::WinTsf, APP_VERSION)).is_err() {
            return ExitCode::from(1);
        }
        let failed = Rc::new(Cell::new(false));
        let rec = make_recorder(file, failed.clone());
        match run_window(rec) {
            Ok(_) if !failed.get() => ExitCode::SUCCESS,
            _ => ExitCode::from(1),
        }
    }
}
