// Carries only the conversion-session state machine.
// `ime_compose`, `ime_cloak` and `ime_mode` are whole files (their SHA-256 hashes are pinned by
// manifest.json). `hook` is a shim exposing only the constants and atomics the state machine reads;
// the three modules themselves are left unmodified.
// Some functions the three modules expose are unused by this logger (for example, a platform-specific
// folding helper), so dead code is allowed here.
#[allow(dead_code)]
pub mod hook;
#[allow(dead_code)]
pub mod ime_cloak;
#[allow(dead_code)]
pub mod ime_compose;
#[allow(dead_code)]
pub mod ime_mode;
