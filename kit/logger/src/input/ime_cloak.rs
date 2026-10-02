// The checksum of this file is pinned by the manifests in kit/logger (manifest*.json).

use std::collections::BTreeSet;

pub fn is_ime_cloak_class(class: &str) -> bool {
    class == "IME"
}

pub fn candidate_shown_from_cloak(cloaked: bool) -> bool {
    !cloaked
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FoldAction {
    Begin(u64),
    End {
        dwell_ms: u64,
        forced: bool,
    },
}

pub const FOLD_GAP_MS: u64 = 150;

pub const MAX_SESSION_MS: u64 = 20_000;

pub(crate) const MONITOR_SAFETY_VALVE_MS: u64 = 25_000;

const _: () = assert!(
    MAX_SESSION_MS + 1_000 <= MONITOR_SAFETY_VALVE_MS,
    "The CloakFold limit must be at least one second inside the safety limit of the monitoring loop."
);

#[derive(Debug)]
pub struct CloakFold {
    shown: BTreeSet<isize>,
    session_start_ms: Option<u64>,
    empty_since_ms: Option<u64>,
    gap_ms: u64,
}

impl Default for CloakFold {
    fn default() -> Self {
        Self::with_gap(FOLD_GAP_MS)
    }
}

impl CloakFold {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_gap(gap_ms: u64) -> Self {
        Self {
            shown: BTreeSet::new(),
            session_start_ms: None,
            empty_since_ms: None,
            gap_ms,
        }
    }

    pub fn is_active(&self) -> bool {
        self.session_start_ms.is_some()
    }

    pub fn on_event(&mut self, hwnd: isize, shown: bool, now_ms: u64) -> Option<FoldAction> {
        if shown {
            self.shown.insert(hwnd);
            self.empty_since_ms = None;
            if self.session_start_ms.is_none() {
                self.session_start_ms = Some(now_ms);
                return Some(FoldAction::Begin(now_ms));
            }
            return None;
        }
        self.shown.remove(&hwnd);
        if self.shown.is_empty() && self.session_start_ms.is_some() && self.empty_since_ms.is_none()
        {
            self.empty_since_ms = Some(now_ms);
        }
        None
    }

    pub fn on_tick(&mut self, now_ms: u64) -> Option<FoldAction> {
        let start = self.session_start_ms?;
        if now_ms.saturating_sub(start) >= MAX_SESSION_MS {
            let end = self.empty_since_ms.unwrap_or(now_ms);
            let dwell = end.saturating_sub(start);
            self.reset();
            return Some(FoldAction::End {
                dwell_ms: dwell,
                forced: true,
            });
        }
        if let Some(empty_since) = self.empty_since_ms {
            if self.shown.is_empty() && now_ms.saturating_sub(empty_since) >= self.gap_ms {
                let dwell = empty_since.saturating_sub(start);
                self.reset();
                return Some(FoldAction::End {
                    dwell_ms: dwell,
                    forced: false,
                });
            }
        }
        None
    }

    fn reset(&mut self) {
        self.shown.clear();
        self.session_start_ms = None;
        self.empty_since_ms = None;
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cloak_class_matches_only_exact_ime() {
        assert!(is_ime_cloak_class("IME"));
    }

    #[test]
    fn cloak_class_rejects_generic_and_similar_classes() {
        assert!(
            !is_ime_cloak_class("Xaml_WindowedPopupClass"),
            "The window shares its class with tooltips, menus and flyouts, so it would be detected falsely."
        );
        assert!(
            !is_ime_cloak_class("ApplicationFrameWindow"),
            "The host frame of every UWP application would be detected falsely."
        );
        assert!(!is_ime_cloak_class("MSCTFIME UI"));
        assert!(!is_ime_cloak_class("IMEClass"));
        assert!(!is_ime_cloak_class("MyIME"));
        assert!(!is_ime_cloak_class("ime"));
        assert!(!is_ime_cloak_class(""));
    }

    #[test]
    fn shown_means_not_cloaked() {
        assert!(
            candidate_shown_from_cloak(false),
            "By the DWM definition, cloaked=0 means that the window is not cloaked, that is, it is being drawn."
        );
        assert!(
            !candidate_shown_from_cloak(true),
            "cloaked=1 means that the DWM is not drawing the window, so the candidates are not visible."
        );
    }

    #[test]
    fn single_window_begin_then_end_after_gap() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 1_000), Some(FoldAction::Begin(1_000)));
        assert!(f.is_active());
        assert_eq!(f.on_event(1, false, 1_700), None, "It must not end immediately.");
        assert_eq!(f.on_tick(1_700 + FOLD_GAP_MS - 1), None);
        assert_eq!(
            f.on_tick(1_700 + FOLD_GAP_MS),
            Some(FoldAction::End {
                dwell_ms: 700,
                forced: false
            }),
            "The dwell time excludes the debounce wait."
        );
        assert!(!f.is_active());
    }

    #[test]
    fn two_cycles_within_gap_fold_into_one_session() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 0), Some(FoldAction::Begin(0)));
        assert_eq!(f.on_event(1, false, 300), None);
        assert_eq!(f.on_tick(300 + FOLD_GAP_MS - 10), None);
        assert_eq!(f.on_event(1, true, 300 + FOLD_GAP_MS - 10), None);
        assert_eq!(f.on_event(1, false, 900), None);
        assert_eq!(
            f.on_tick(900 + FOLD_GAP_MS),
            Some(FoldAction::End {
                dwell_ms: 900,
                forced: false
            }),
            "Two cycles are merged into one row (the dwell time runs to the last disappearance)."
        );
    }

    #[test]
    fn overlapping_windows_merge_into_one_session() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 0), Some(FoldAction::Begin(0)));
        assert_eq!(f.on_event(2, true, 100), None, "The second one does not produce a Begin.");
        assert_eq!(f.on_event(1, false, 400), None);
        assert_eq!(f.on_tick(400 + FOLD_GAP_MS + 100), None, "It is not empty yet.");
        assert_eq!(f.on_event(2, false, 800), None);
        assert_eq!(
            f.on_tick(800 + FOLD_GAP_MS),
            Some(FoldAction::End {
                dwell_ms: 800,
                forced: false
            })
        );
    }

    #[test]
    fn reentry_after_gap_starts_new_session() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 0), Some(FoldAction::Begin(0)));
        assert_eq!(f.on_event(1, false, 500), None);
        assert_eq!(
            f.on_tick(500 + FOLD_GAP_MS),
            Some(FoldAction::End {
                dwell_ms: 500,
                forced: false
            })
        );
        let t = 500 + FOLD_GAP_MS + 1;
        assert_eq!(f.on_event(1, true, t), Some(FoldAction::Begin(t)));
    }

    #[test]
    fn session_self_heals_after_max_duration() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 0), Some(FoldAction::Begin(0)));
        assert_eq!(f.on_tick(MAX_SESSION_MS - 1), None);
        assert_eq!(
            f.on_tick(MAX_SESSION_MS),
            Some(FoldAction::End {
                dwell_ms: MAX_SESSION_MS,
                forced: true
            }),
            "A forced termination must set forced=true so that it produces no row."
        );
        assert!(!f.is_active(), "The limit always releases it.");
    }


    const RECORDED_IME_EVENTS: &[(u64, isize, bool)] = &[
        (7180, 3, true),
        (7180, 4, true),
        (10521, 3, false),
        (10521, 4, false),
        (12904, 3, true),
        (12904, 4, true),
        (16142, 3, false),
        (16142, 4, false),
        (16487, 3, true),
        (16487, 4, true),
        (19516, 3, false),
        (19516, 4, false),
        (19824, 3, true),
        (19824, 4, true),
        (21114, 3, false),
        (21114, 4, false),
        (22337, 3, true),
        (22337, 4, true),
        (24298, 3, false),
        (24298, 4, false),
        (24649, 3, true),
        (24649, 4, true),
        (25669, 3, false),
        (25669, 4, false),
        (26854, 3, true),
        (26854, 4, true),
        (28926, 3, false),
        (28926, 4, false),
        (30536, 3, true),
        (30536, 4, true),
        (32454, 3, false),
        (32454, 4, false),
        (33172, 3, true),
        (33172, 4, true),
        (35585, 3, false),
        (35585, 4, false),
        (38172, 3, true),
        (38172, 4, true),
        (42272, 3, false),
        (42272, 4, false),
        (42873, 3, true),
        (42873, 4, true),
        (45194, 3, false),
        (45194, 4, false),
        (47580, 3, true),
        (47580, 4, true),
        (52129, 3, false),
        (52129, 4, false),
    ];

    fn replay(gap_ms: u64) -> (Vec<u64>, u32) {
        let tick = gap_ms.max(1) / 4 + 1;
        let mut fold = CloakFold::with_gap(gap_ms);
        let mut dwells = Vec::new();
        let mut forced = 0u32;
        let mut sink = |a: FoldAction| match a {
            FoldAction::End {
                dwell_ms,
                forced: true,
            } => {
                let _ = dwell_ms;
                forced += 1;
            }
            FoldAction::End {
                dwell_ms,
                forced: false,
            } => dwells.push(dwell_ms),
            FoldAction::Begin(_) => {}
        };
        let mut clock = RECORDED_IME_EVENTS[0].0;
        for (t, hwnd, shown) in RECORDED_IME_EVENTS {
            while clock + tick <= *t {
                clock += tick;
                if let Some(a) = fold.on_tick(clock) {
                    sink(a);
                }
            }
            clock = *t;
            if let Some(a) = fold.on_event(*hwnd, *shown, *t) {
                sink(a);
            }
            if let Some(a) = fold.on_tick(*t) {
                sink(a);
            }
        }
        let last = RECORDED_IME_EVENTS[RECORDED_IME_EVENTS.len() - 1].0;
        while clock <= last + gap_ms + MAX_SESSION_MS {
            clock += tick;
            if let Some(a) = fold.on_tick(clock) {
                sink(a);
            }
        }
        (dwells, forced)
    }

    #[test]
    fn recorded_session_yields_one_row_per_conversion() {
        let (dwells, forced) = replay(FOLD_GAP_MS);
        assert_eq!(
            dwells.len(),
            12,
            "The count must equal the measured number of conversions (dwell times obtained: {dwells:?})."
        );
        assert_eq!(forced, 0, "A forced termination must not occur on real data.");
        assert!(
            dwells.iter().all(|d| *d >= 300),
            "A row shorter than 300 ms suggests a captured flicker: {dwells:?}"
        );
    }

    #[test]
    fn too_large_a_debounce_merges_separate_conversions() {
        assert_eq!(replay(FOLD_GAP_MS).0.len(), 12, "The adopted value yields 12 rows.");
        assert!(
            replay(400).0.len() < 12,
            "Merging should occur at 400 ms (if this boundary moves, re-measure the basis of FOLD_GAP_MS)."
        );
    }

    #[test]
    fn hide_without_session_is_ignored() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, false, 100), None);
        assert!(!f.is_active());
        assert_eq!(f.on_tick(100_000), None, "Nothing happens.");
    }

    #[test]
    fn duplicate_shown_does_not_double_begin() {
        let mut f = CloakFold::new();
        assert_eq!(f.on_event(1, true, 0), Some(FoldAction::Begin(0)));
        assert_eq!(f.on_event(1, true, 50), None);
        assert_eq!(f.on_event(1, false, 200), None);
        assert_eq!(
            f.on_tick(200 + FOLD_GAP_MS),
            Some(FoldAction::End {
                dwell_ms: 200,
                forced: false
            })
        );
    }
}
