// The checksum of this file is pinned by the manifests in kit/logger (manifest*.json).

use super::hook::VK_KANJI;

pub const MAX_SESSION_MS: u64 = super::ime_cloak::MAX_SESSION_MS;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Chord {
    Plain,
    Modified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ComposeSummary {
    pub dwell_ms: u64,
    pub nav: u32,
    pub abandon: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ComposeEnd {
    Emit(ComposeSummary),
    Truncated {
        dwell_ms: u64,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ComposeState {
    pub session_start_ms: Option<u64>,
    pub active: bool,
    pub nav: u32,
}


impl ComposeState {
    pub fn is_composing(&self) -> bool {
        self.session_start_ms.is_some()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ComposeStep {
    pub next: ComposeState,
    pub end: Option<ComposeEnd>,
}

pub fn step(prev: ComposeState, vk: u32, ime_open: bool, chord: Chord, now_ms: u64) -> ComposeStep {
    if let Some(start) = prev.session_start_ms {
        if now_ms.saturating_sub(start) >= MAX_SESSION_MS {
            let truncated = ComposeEnd::Truncated {
                dwell_ms: now_ms.saturating_sub(start),
            };
            let restarted = apply(ComposeState::default(), vk, ime_open, chord, now_ms);
            return ComposeStep {
                next: restarted.next,
                end: Some(truncated),
            };
        }
    }

    apply(prev, vk, ime_open, chord, now_ms)
}

fn apply(prev: ComposeState, vk: u32, ime_open: bool, chord: Chord, now_ms: u64) -> ComposeStep {
    if !ime_open {
        return finish(prev, false, now_ms);
    }

    if matches!(chord, Chord::Modified) {
        return ComposeStep {
            next: prev,
            end: None,
        };
    }

    match vk {
        0x30..=0x39 | 0x41..=0x5A => {
            let mut next = prev;
            next.active = false;
            if next.session_start_ms.is_none() {
                next.session_start_ms = Some(now_ms);
                next.nav = 0;
            }
            ComposeStep { next, end: None }
        }
        0x20 => {
            let mut next = prev;
            if prev.session_start_ms.is_some() && !prev.active {
                next.active = true;
            }
            ComposeStep { next, end: None }
        }
        0x0D => finish(prev, false, now_ms),
        0x1B => finish(prev, true, now_ms),
        0x08 => {
            let mut next = prev;
            next.active = false;
            ComposeStep { next, end: None }
        }
        0x25..=0x28 => {
            let mut next = prev;
            if prev.session_start_ms.is_some() {
                next.nav = next.nav.saturating_add(1);
            }
            ComposeStep { next, end: None }
        }
        _ if vk >= 0xF0 || vk == VK_KANJI => ComposeStep {
            next: prev,
            end: None,
        },
        _ => finish(prev, false, now_ms),
    }
}

fn finish(prev: ComposeState, abandon: bool, now_ms: u64) -> ComposeStep {
    let Some(start) = prev.session_start_ms else {
        return ComposeStep {
            next: ComposeState::default(),
            end: None,
        };
    };
    ComposeStep {
        next: ComposeState::default(),
        end: Some(ComposeEnd::Emit(ComposeSummary {
            dwell_ms: now_ms.saturating_sub(start),
            nav: prev.nav,
            abandon,
        })),
    }
}


use std::sync::atomic::Ordering;

use super::hook::{
    IME_ACTIVE, IME_CANDIDATE_NAV_COUNT, IME_COMPOSE_NAV, IME_COMPOSING, IME_COMPOSITION_START_MS,
    IME_SHADOW_ABANDON, IME_SHADOW_DWELL_MS, IME_SHADOW_READY,
};

pub fn drive(vk: u32, ime_open: bool, chord: Chord, now_ms: u64) {
    let ComposeStep { next, end } = step(load(), vk, ime_open, chord, now_ms);

    IME_COMPOSITION_START_MS.store(next.session_start_ms.unwrap_or(0), Ordering::Relaxed);
    IME_COMPOSE_NAV.store(next.nav, Ordering::Relaxed);
    IME_COMPOSING.store(next.is_composing(), Ordering::Release);
    IME_ACTIVE.store(next.active, Ordering::Release);

    if let Some(ComposeEnd::Emit(summary)) = end {
        IME_SHADOW_DWELL_MS.store(summary.dwell_ms, Ordering::Relaxed);
        IME_CANDIDATE_NAV_COUNT.store(summary.nav, Ordering::Relaxed);
        IME_SHADOW_ABANDON.store(summary.abandon, Ordering::Relaxed);
        IME_SHADOW_READY.store(true, Ordering::Release);
    }
}

pub fn should_drain_ready(ready: bool, active: bool, is_windows: bool) -> bool {
    if !ready {
        return false;
    }
    if is_windows {
        !active
    } else {
        true
    }
}

fn load() -> ComposeState {
    let composing = IME_COMPOSING.load(Ordering::Acquire);
    ComposeState {
        session_start_ms: composing.then(|| IME_COMPOSITION_START_MS.load(Ordering::Relaxed)),
        active: IME_ACTIVE.load(Ordering::Acquire),
        nav: if composing {
            IME_COMPOSE_NAV.load(Ordering::Relaxed)
        } else {
            0
        },
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    const ENTER: u32 = 0x0D;
    const ESC: u32 = 0x1B;
    const SPACE: u32 = 0x20;
    const BACKSPACE: u32 = 0x08;
    const LEFT: u32 = 0x25;
    const RIGHT: u32 = 0x27;
    const A: u32 = 0x41;
    const K: u32 = 0x4B;
    const TAB: u32 = 0x09;

    fn run(keys: &[(u32, bool, u64)]) -> (ComposeState, Vec<ComposeEnd>) {
        let chorded: Vec<_> = keys
            .iter()
            .map(|&(vk, open, t)| (vk, open, Chord::Plain, t))
            .collect();
        run_chords(&chorded)
    }

    fn run_chords(keys: &[(u32, bool, Chord, u64)]) -> (ComposeState, Vec<ComposeEnd>) {
        let mut state = ComposeState::default();
        let mut ends = Vec::new();
        for &(vk, ime_open, chord, t) in keys {
            let s = step(state, vk, ime_open, chord, t);
            state = s.next;
            if let Some(e) = s.end {
                ends.push(e);
            }
        }
        (state, ends)
    }

    fn emitted(ends: &[ComposeEnd]) -> Vec<ComposeSummary> {
        ends.iter()
            .filter_map(|e| match e {
                ComposeEnd::Emit(s) => Some(*s),
                ComposeEnd::Truncated { .. } => None,
            })
            .collect()
    }


    #[test]
    fn enter_confirms_the_session() {
        let (_, ends) = run(&[(A, true, 100), (SPACE, true, 300), (ENTER, true, 900)]);
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 800,
                nav: 0,
                abandon: false,
            }]
        );
    }

    #[test]
    fn escape_abandons_the_session() {
        let (_, ends) = run(&[(A, true, 100), (SPACE, true, 300), (ESC, true, 900)]);
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 800,
                nav: 0,
                abandon: true,
            }]
        );
    }

    #[test]
    fn confirm_and_abandon_differ_only_by_the_terminating_key() {
        let (_, confirmed) = run(&[(A, true, 0), (SPACE, true, 100), (ENTER, true, 500)]);
        let (_, abandoned) = run(&[(A, true, 0), (SPACE, true, 100), (ESC, true, 500)]);
        let c = emitted(&confirmed);
        let a = emitted(&abandoned);
        assert_eq!(c[0].dwell_ms, a[0].dwell_ms);
        assert_eq!(c[0].nav, a[0].nav);
        assert_ne!(c[0].abandon, a[0].abandon);
    }


    #[test]
    fn dwell_starts_at_the_first_alphanumeric_key() {
        let (_, ends) = run(&[
            (A, true, 1_000),
            (K, true, 1_200),
            (SPACE, true, 1_500),
            (ENTER, true, 2_000),
        ]);
        assert_eq!(emitted(&ends)[0].dwell_ms, 1_000);
    }

    #[test]
    fn typing_during_candidates_does_not_close_the_session() {
        let (state, ends) = run(&[
            (A, true, 0),
            (SPACE, true, 100),
            (K, true, 200),
            (SPACE, true, 300),
            (ENTER, true, 400),
        ]);
        assert_eq!(emitted(&ends).len(), 1, "Each conversion must produce exactly one row.");
        assert_eq!(emitted(&ends)[0].dwell_ms, 400);
        assert!(!state.is_composing());
    }

    #[test]
    fn backspace_leaves_candidates_but_keeps_the_session() {
        let (state, ends) = run(&[(A, true, 0), (SPACE, true, 100), (BACKSPACE, true, 200)]);
        assert!(ends.is_empty(), "Backspace must not produce a row.");
        assert!(state.is_composing());
        assert!(!state.active);
    }

    #[test]
    fn other_keys_close_the_session_as_confirmed() {
        let (_, ends) = run(&[(A, true, 0), (TAB, true, 700)]);
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 700,
                nav: 0,
                abandon: false,
            }]
        );
    }

    #[test]
    fn leaving_japanese_mode_closes_the_session_as_confirmed() {
        let (state, ends) = run(&[(A, true, 0), (A, false, 600)]);
        assert!(!emitted(&ends)[0].abandon);
        assert_eq!(emitted(&ends)[0].dwell_ms, 600);
        assert!(!state.is_composing());
    }

    #[test]
    fn terminating_keys_outside_a_session_emit_nothing() {
        let (_, ends) = run(&[(ENTER, true, 10), (ESC, true, 20), (TAB, true, 30)]);
        assert!(ends.is_empty());
    }

    #[test]
    fn nothing_is_emitted_while_ime_is_closed() {
        let (state, ends) = run(&[(A, false, 0), (K, false, 100), (ENTER, false, 200)]);
        assert!(ends.is_empty());
        assert!(!state.is_composing());
    }


    #[test]
    fn arrows_are_counted_within_the_session() {
        let (_, ends) = run(&[
            (LEFT, true, 0),
            (A, true, 100),
            (SPACE, true, 200),
            (RIGHT, true, 300),
            (RIGHT, true, 400),
            (LEFT, true, 500),
            (ENTER, true, 600),
        ]);
        assert_eq!(emitted(&ends)[0].nav, 3);
    }

    #[test]
    fn nav_does_not_leak_into_the_next_session() {
        let (_, ends) = run(&[
            (A, true, 0),
            (RIGHT, true, 100),
            (ENTER, true, 200),
            (A, true, 300),
            (ENTER, true, 400),
        ]);
        let rows = emitted(&ends);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].nav, 1);
        assert_eq!(rows[1].nav, 0, "Navigation state from the previous session has leaked.");
    }

    #[test]
    fn abandon_does_not_leak_into_the_next_session() {
        let (_, ends) = run(&[
            (A, true, 0),
            (ESC, true, 100),
            (A, true, 200),
            (ENTER, true, 300),
        ]);
        let rows = emitted(&ends);
        assert_eq!(rows.len(), 2);
        assert!(rows[0].abandon);
        assert!(!rows[1].abandon, "Abandon state from the previous session has leaked.");
    }


    #[test]
    fn a_command_chord_does_not_open_a_session() {
        let (state, ends) = run_chords(&[
            (0x53, true, Chord::Modified, 0),
            (0x41, true, Chord::Modified, 100),
        ]);
        assert!(
            !state.is_composing(),
            "A session was started by a shortcut key."
        );
        assert!(ends.is_empty());
    }

    #[test]
    fn a_command_chord_does_not_forge_an_abandon_row() {
        let (state, ends) = run_chords(&[
            (A, true, Chord::Plain, 0),
            (ESC, true, Chord::Modified, 300),
        ]);
        assert!(ends.is_empty(), "A modified Escape key produced an abandon row.");
        assert!(state.is_composing(), "The session was closed improperly.");
    }

    #[test]
    fn a_command_chord_mid_session_is_transparent() {
        let (_, ends) = run_chords(&[
            (A, true, Chord::Plain, 1_000),
            (0x53, true, Chord::Modified, 1_200),
            (SPACE, true, Chord::Plain, 1_400),
            (ENTER, true, Chord::Plain, 1_800),
        ]);
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 800,
                nav: 0,
                abandon: false,
            }],
            "A modified keystroke has corrupted the dwell time."
        );
    }

    #[test]
    fn ime_conversion_chords_keep_the_session_alive() {
        let (state, ends) = run_chords(&[
            (A, true, Chord::Plain, 0),
            (0x4A, true, Chord::Modified, 100),
            (0x4B, true, Chord::Modified, 200),
        ]);
        assert!(ends.is_empty());
        assert!(state.is_composing());
    }

    #[test]
    fn a_modified_key_after_leaving_japanese_mode_closes_the_session() {
        let (state, ends) = run_chords(&[
            (A, true, Chord::Plain, 0),
            (0x43, false, Chord::Modified, 500),
        ]);
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 500,
                nav: 0,
                abandon: false,
            }],
            "The session is still open after leaving the mode (the dwell time would keep growing)."
        );
        assert!(!state.is_composing());
    }

    #[test]
    fn a_mouse_confirmed_conversion_merges_into_the_next_one() {
        let (_, ends) = run(&[
            (A, true, 0),
            (SPACE, true, 200),
            (K, true, 3_000),
            (ENTER, true, 3_400),
        ]);
        let rows = emitted(&ends);
        assert_eq!(rows.len(), 1, "Two conversions are merged into one row (a known limitation).");
        assert_eq!(
            rows[0].dwell_ms, 3_400,
            "The dwell time spans two conversions and must not be read as one."
        );
    }

    #[test]
    fn truncation_still_fires_on_a_modified_key() {
        let (_, ends) = run_chords(&[
            (A, true, Chord::Plain, 0),
            (0x53, true, Chord::Modified, MAX_SESSION_MS + 1),
        ]);
        assert!(matches!(ends[0], ComposeEnd::Truncated { .. }));
    }


    #[test]
    fn over_long_sessions_are_truncated_without_a_row() {
        let (_, ends) = run(&[(A, true, 0), (ENTER, true, MAX_SESSION_MS)]);
        assert_eq!(
            ends,
            vec![ComposeEnd::Truncated {
                dwell_ms: MAX_SESSION_MS
            }]
        );
        assert!(emitted(&ends).is_empty(), "A forced termination must not produce a row.");
    }

    #[test]
    fn just_under_the_cap_still_emits() {
        let (_, ends) = run(&[(A, true, 0), (ENTER, true, MAX_SESSION_MS - 1)]);
        assert_eq!(emitted(&ends)[0].dwell_ms, MAX_SESSION_MS - 1);
    }

    #[test]
    fn a_key_after_truncation_starts_a_fresh_session() {
        let (state, ends) = run(&[
            (A, true, 0),
            (K, true, MAX_SESSION_MS + 5),
            (ENTER, true, MAX_SESSION_MS + 205),
        ]);
        assert!(matches!(ends[0], ComposeEnd::Truncated { .. }));
        assert_eq!(
            emitted(&ends),
            vec![ComposeSummary {
                dwell_ms: 200,
                nav: 0,
                abandon: false,
            }]
        );
        assert!(!state.is_composing());
    }


    #[test]
    fn active_never_outlives_the_session() {
        let mut state = ComposeState::default();
        for &(vk, open, t) in &[
            (SPACE, true, 0),
            (A, true, 10),
            (SPACE, true, 20),
            (ESC, true, 30),
            (SPACE, true, 40),
        ] {
            state = step(state, vk, open, Chord::Plain, t).next;
            assert!(
                !state.active || state.is_composing(),
                "Composition is active but no session exists (invariant 1 is violated)."
            );
        }
    }

    #[test]
    fn a_terminating_key_repairs_a_broken_invariant() {
        let broken = ComposeState {
            session_start_ms: None,
            active: true,
            nav: 7,
        };
        for &vk in &[ENTER, ESC, TAB] {
            let s = step(broken, vk, true, Chord::Plain, 100);
            assert_eq!(
                s.next,
                ComposeState::default(),
                "The state was not cleared at vk=0x{vk:02X}."
            );
            assert!(s.end.is_none(), "A row must not be produced when there is no session.");
        }
        let s = step(broken, A, false, Chord::Plain, 100);
        assert_eq!(s.next, ComposeState::default());
        assert!(s.end.is_none());
    }

    #[test]
    fn space_alone_does_not_open_a_session() {
        let (state, ends) = run(&[(SPACE, true, 0), (SPACE, true, 100)]);
        assert!(!state.is_composing());
        assert!(!state.active);
        assert!(ends.is_empty());
    }

    #[test]
    fn space_during_candidates_stays_active() {
        let (state, ends) = run(&[(A, true, 0), (SPACE, true, 100), (SPACE, true, 200)]);
        assert!(state.active);
        assert!(state.is_composing());
        assert!(ends.is_empty());
    }

    #[test]
    fn ime_mode_keys_are_transparent() {
        let (state, ends) = run(&[(A, true, 0), (VK_KANJI, true, 100), (0xF2, true, 150)]);
        assert!(ends.is_empty());
        assert!(state.is_composing());
    }
}


#[cfg(test)]
mod driver_tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    static DRIVER_LOCK: Mutex<()> = Mutex::new(());

    const ENTER: u32 = 0x0D;
    const ESC: u32 = 0x1B;
    const SPACE: u32 = 0x20;
    const RIGHT: u32 = 0x27;
    const A: u32 = 0x41;
    const K: u32 = 0x4B;

    fn acquire() -> MutexGuard<'static, ()> {
        let guard = DRIVER_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        IME_COMPOSITION_START_MS.store(0, Ordering::Relaxed);
        IME_COMPOSING.store(false, Ordering::Release);
        IME_ACTIVE.store(false, Ordering::Release);
        IME_COMPOSE_NAV.store(0, Ordering::Relaxed);
        IME_CANDIDATE_NAV_COUNT.store(0, Ordering::Relaxed);
        IME_SHADOW_ABANDON.store(false, Ordering::Relaxed);
        IME_SHADOW_READY.store(false, Ordering::Release);
        guard
    }

    fn collect_row() -> Option<ComposeSummary> {
        if !IME_SHADOW_READY.swap(false, Ordering::AcqRel) {
            return None;
        }
        Some(ComposeSummary {
            dwell_ms: IME_SHADOW_DWELL_MS.swap(0, Ordering::Relaxed),
            nav: IME_CANDIDATE_NAV_COUNT.swap(0, Ordering::Relaxed),
            abandon: IME_SHADOW_ABANDON.swap(false, Ordering::Relaxed),
        })
    }

    #[test]
    fn driver_emits_a_confirmed_row() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 100);
        drive(SPACE, true, Chord::Plain, 300);
        drive(ENTER, true, Chord::Plain, 900);
        assert_eq!(
            collect_row(),
            Some(ComposeSummary {
                dwell_ms: 800,
                nav: 0,
                abandon: false,
            })
        );
        assert!(!IME_COMPOSING.load(Ordering::Acquire));
        assert!(!IME_ACTIVE.load(Ordering::Acquire));
    }

    #[test]
    fn driver_emits_an_abandoned_row() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 100);
        drive(SPACE, true, Chord::Plain, 300);
        drive(RIGHT, true, Chord::Plain, 400);
        drive(ESC, true, Chord::Plain, 900);
        assert_eq!(
            collect_row(),
            Some(ComposeSummary {
                dwell_ms: 800,
                nav: 1,
                abandon: true,
            })
        );
    }

    #[test]
    fn a_keystroke_after_the_session_does_not_erase_the_pending_row() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 100);
        drive(ESC, true, Chord::Plain, 500);
        drive(K, false, Chord::Plain, 520);
        drive(K, false, Chord::Plain, 540);
        assert_eq!(
            collect_row(),
            Some(ComposeSummary {
                dwell_ms: 400,
                nav: 0,
                abandon: true,
            }),
            "The abandon flag was cleared by a keystroke before it was collected."
        );
    }

    #[test]
    fn a_new_session_before_collection_does_not_forge_the_pending_row() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 0);
        drive(RIGHT, true, Chord::Plain, 100);
        drive(ESC, true, Chord::Plain, 400);
        drive(A, true, Chord::Plain, 450);
        drive(SPACE, true, Chord::Plain, 480);
        assert_eq!(
            collect_row(),
            Some(ComposeSummary {
                dwell_ms: 400,
                nav: 1,
                abandon: true,
            }),
            "The start of the next conversion has disguised a row that was waiting to be collected."
        );
        assert!(IME_COMPOSING.load(Ordering::Acquire));
        assert!(IME_ACTIVE.load(Ordering::Acquire));
    }

    #[test]
    fn driver_mirrors_the_session_fields() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 7_000);
        assert!(IME_COMPOSING.load(Ordering::Acquire));
        assert_eq!(IME_COMPOSITION_START_MS.load(Ordering::Relaxed), 7_000);
        assert!(!IME_ACTIVE.load(Ordering::Acquire));
        drive(SPACE, true, Chord::Plain, 7_100);
        assert!(IME_ACTIVE.load(Ordering::Acquire), "Space opens the candidate window.");
        assert!(
            collect_row().is_none(),
            "A row must not be produced in the middle of a session."
        );
    }

    #[test]
    fn driver_emits_nothing_when_truncated() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 0);
        drive(ENTER, true, Chord::Plain, MAX_SESSION_MS + 1);
        assert!(collect_row().is_none(), "A forced termination must not produce a row.");
        assert!(!IME_COMPOSING.load(Ordering::Acquire));
    }

    #[test]
    fn a_new_session_resets_nav() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 0);
        drive(RIGHT, true, Chord::Plain, 50);
        drive(RIGHT, true, Chord::Plain, 60);
        drive(ENTER, true, Chord::Plain, 100);
        let first = collect_row().expect("The first row was not produced.");
        assert_eq!(first.nav, 2);
        drive(A, true, Chord::Plain, 200);
        drive(ENTER, true, Chord::Plain, 300);
        assert_eq!(collect_row().expect("The second row was not produced.").nav, 0);
    }

    #[test]
    fn drain_policy_differs_by_os() {
        assert!(!should_drain_ready(false, false, true));
        assert!(!should_drain_ready(false, false, false));
        assert!(should_drain_ready(true, false, true));
        assert!(!should_drain_ready(true, true, true));
        assert!(should_drain_ready(true, false, false));
        assert!(should_drain_ready(true, true, false));
    }

    #[test]
    fn an_external_active_reset_does_not_lose_the_session() {
        let _g = acquire();
        drive(A, true, Chord::Plain, 0);
        drive(SPACE, true, Chord::Plain, 100);
        IME_ACTIVE.store(false, Ordering::Release);
        drive(ENTER, true, Chord::Plain, 400);
        assert_eq!(
            collect_row(),
            Some(ComposeSummary {
                dwell_ms: 400,
                nav: 0,
                abandon: false,
            })
        );
    }
}
