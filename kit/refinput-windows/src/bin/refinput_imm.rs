//! Reference input field for Windows, (a) IMM compatibility layer: records this window's own
//! IME messages as content-free `gt/1` lines (`src: "win_imm"`).
//!
//! Usage (the result is reported only through the exit code; nothing is written to the
//! console):
//!   refinput_imm --out <path>              Creates one window and records until it is closed
//!                                           (an existing <path> is never overwritten).
//!   refinput_imm --selftest [--out <path>] Creates no window; writes a representative sequence
//!                                           through the core and reads it back for validation.
//!
//! Exit code: 0 success / 1 a runtime failure (window, write, or self-test mismatch) / 2 usage
//! error, or not running on Windows.
//!
//! No text is ever captured. A length is obtained by calling `ImmGetCompositionStringW` with a
//! null buffer and a length of 0, which yields only a byte count. A message that carries text
//! (such as `WM_IME_CHAR`) is not recorded. Messages are recorded as received, in the order
//! received, with no interpretation; which messages actually arrive is confirmed separately, in
//! a calibration pilot using a real input method engine.

use std::process::ExitCode;

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
    use std::ffi::OsString;
    use std::fs::{File, OpenOptions};
    use std::io::{Read, Write};
    use std::path::{Path, PathBuf};
    use std::process::ExitCode;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Mutex, OnceLock};
    use std::time::{SystemTime, UNIX_EPOCH};

    use refinput_windows::{check_line, header, line, ticks_to_ns, units_from_bytes, Ev, Src};
    use windows::core::{w, PCWSTR};
    use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
    use windows::Win32::Graphics::Gdi::{GetSysColorBrush, UpdateWindow, COLOR_WINDOW};
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
    use windows::Win32::UI::Input::Ime::{
        ImmGetCompositionStringW, ImmGetContext, ImmReleaseContext, GCS_COMPSTR, GCS_RESULTSTR,
        IME_COMPOSITION_STRING,
    };
    use windows::Win32::UI::Input::KeyboardAndMouse::SetFocus;
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, LoadCursorW,
        PostQuitMessage, RegisterClassExW, ShowWindow, TranslateMessage, CS_HREDRAW, CS_VREDRAW,
        CW_USEDEFAULT, IDC_ARROW, MSG, SW_SHOW, WM_DESTROY, WM_IME_COMPOSITION,
        WM_IME_ENDCOMPOSITION, WM_IME_NOTIFY, WM_IME_STARTCOMPOSITION, WM_KEYDOWN, WM_KILLFOCUS,
        WM_SETFOCUS, WNDCLASSEXW, WS_OVERLAPPEDWINDOW,
    };

    const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

    /// A write failure. The window procedure cannot return a value, so this is held as a flag
    /// and turned into the exit code instead.
    static WRITE_FAILED: AtomicBool = AtomicBool::new(false);
    static RECORDER: OnceLock<Mutex<Recorder>> = OnceLock::new();

    // ---- recording (clock and output) ------------------------------------------------------

    /// The output file and the monotonic clock's frequency.
    struct Recorder {
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

    /// Records one event, together with the time at which it arrived.
    fn record(ev: Ev) {
        let Some(cell) = RECORDER.get() else { return };
        let mut rec = match cell.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
        let text = line(ev, wall_ms(), mono_ns(rec.qpc_freq));
        if append(&mut rec.file, &text).is_err() {
            WRITE_FAILED.store(true, Ordering::SeqCst);
            // SAFETY: a simple call with no arguments, made from within the window procedure on
            // this window's own thread.
            unsafe { PostQuitMessage(1) };
        }
    }

    // ---- IMM calls (unsafe is confined to this section) -------------------------------------

    /// The window's input context (pairs acquisition with release).
    struct ImeContext {
        hwnd: HWND,
        himc: windows::Win32::UI::Input::Ime::HIMC,
    }

    impl ImeContext {
        fn of(hwnd: HWND) -> Option<ImeContext> {
            // SAFETY: `hwnd` is this window's own handle, as passed to its window procedure.
            let himc = unsafe { ImmGetContext(hwnd) };
            if himc.is_invalid() {
                None
            } else {
                Some(ImeContext { hwnd, himc })
            }
        }

        /// Only the length of the string of the given kind, as a count of UTF-16 code units.
        /// The buffer is null and the length is 0, so the string itself is never obtained.
        fn len_of(&self, kind: IME_COMPOSITION_STRING) -> u32 {
            // SAFETY: `himc` has been acquired and not yet released. With a null buffer and a
            // length of 0, only a byte count is returned.
            units_from_bytes(unsafe { ImmGetCompositionStringW(self.himc, kind, None, 0) })
        }
    }

    impl Drop for ImeContext {
        fn drop(&mut self) {
            // SAFETY: returns the pair obtained in `of` to the same window.
            let _ = unsafe { ImmReleaseContext(self.hwnd, self.himc) };
        }
    }

    /// Records `WM_IME_COMPOSITION`: writes the `lParam` flags, and obtains only the lengths for
    /// the kinds whose bit is set. `wParam` carries the most recent character (DBCS) for this
    /// message and is never read.
    fn record_composition(hwnd: HWND, lparam: LPARAM) {
        let flags = lparam.0 as u32;
        let ctx = ImeContext::of(hwnd);
        let len_if = |bit: IME_COMPOSITION_STRING| -> u32 {
            match &ctx {
                Some(c) if flags & bit.0 != 0 => c.len_of(bit),
                _ => 0,
            }
        };
        record(Ev::Comp {
            flags,
            comp_len: len_if(GCS_COMPSTR),
            result_len: len_if(GCS_RESULTSTR),
        });
    }

    // ---- window --------------------------------------------------------------------------------

    /// Records a message and then passes it on to `DefWindowProcW`, so the input method's
    /// default display is not disturbed. A message that carries text (such as `WM_IME_CHAR`) is
    /// left to the default processing and is never handled here.
    unsafe extern "system" fn wnd_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match msg {
            WM_IME_STARTCOMPOSITION => record(Ev::StartComp),
            WM_IME_COMPOSITION => record_composition(hwnd, lparam),
            WM_IME_ENDCOMPOSITION => record(Ev::EndComp),
            WM_IME_NOTIFY => record(Ev::Notify {
                imn: wparam.0 as u32,
            }),
            WM_KEYDOWN => record(Ev::KeyDown {
                vk: wparam.0 as u32,
            }),
            WM_SETFOCUS => record(Ev::FocusIn),
            WM_KILLFOCUS => record(Ev::FocusOut),
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

    /// Creates one window and pumps messages until it is closed.
    fn run_window() -> windows::core::Result<i32> {
        // SAFETY: Win32 window creation and the message loop, entirely self-contained within
        // this function (and this thread).
        unsafe {
            let hinstance: HINSTANCE = GetModuleHandleW(PCWSTR::null())?.into();
            let class_name = w!("RefInputImm");
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
                w!("refinput_imm (gt/1 win_imm)"),
                WS_OVERLAPPEDWINDOW,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                640,
                240,
                None,
                None,
                hinstance,
                None,
            )?;
            let _ = ShowWindow(hwnd, SW_SHOW);
            let _ = UpdateWindow(hwnd);
            let _ = SetFocus(hwnd);

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
                // The input method receives keys through `TranslateMessage`; this call is not
                // used to record any character.
                let _ = TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
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

    /// The representative sequence the self-test writes (no window is created; only the core is
    /// exercised). Timestamps are fixed values.
    fn selftest_events() -> Vec<Ev> {
        vec![
            Ev::FocusIn,
            Ev::KeyDown { vk: 0xE5 },
            Ev::StartComp,
            Ev::Notify { imn: 5 },
            Ev::Comp {
                flags: GCS_COMPSTR.0,
                comp_len: 1,
                result_len: 0,
            },
            Ev::Comp {
                flags: GCS_COMPSTR.0,
                comp_len: 2,
                result_len: 0,
            },
            Ev::Notify { imn: 4 },
            Ev::Comp {
                flags: GCS_RESULTSTR.0,
                comp_len: 0,
                result_len: 2,
            },
            Ev::EndComp,
            Ev::KeyDown { vk: 13 },
            Ev::FocusOut,
        ]
    }

    /// Writes the sequence through the core and reads it back for validation. Passing this
    /// check does not by itself establish that a real input method engine would deliver the
    /// same messages; that is confirmed separately, in a calibration pilot.
    fn selftest(path: &Path) -> bool {
        let mut lines = vec![header(Src::WinImm, APP_VERSION)];
        for (i, ev) in selftest_events().into_iter().enumerate() {
            let i = i as u64;
            lines.push(line(
                ev,
                1_700_000_000_000 + i * 10,
                1_000_000_000 + i * 10_000_000,
            ));
        }
        let written = File::create(path).and_then(|mut f| {
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
        let head_ok = it
            .next()
            .is_some_and(|h| h.contains("\"kind\":\"header\"") && check_line(h).is_ok());
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
                        "refinput_imm_selftest_{}.jsonl",
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
        if append(&mut file, &header(Src::WinImm, APP_VERSION)).is_err() {
            return ExitCode::from(1);
        }
        let _ = RECORDER.set(Mutex::new(Recorder {
            file,
            qpc_freq: qpc_freq(),
        }));
        match run_window() {
            Ok(_) if !WRITE_FAILED.load(Ordering::SeqCst) => ExitCode::SUCCESS,
            _ => ExitCode::from(1),
        }
    }
}
