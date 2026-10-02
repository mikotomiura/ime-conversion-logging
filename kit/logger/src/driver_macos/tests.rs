//! Tests of the pure parts of the macOS driver: the key-code mapping, the transitions of the
//! input-mode flag, the modifier rule and the drop counting. Every test works on cells or channels
//! that it creates itself, so none of them touches the process-wide statics that the copied state
//! machine tests use in parallel.

use super::*;
use std::sync::mpsc::sync_channel;

fn cells() -> (AtomicBool, AtomicBool, AtomicBool) {
    (
        AtomicBool::new(false),
        AtomicBool::new(false),
        AtomicBool::new(false),
    )
}

fn press_release(vk: u32, initial_open: bool) -> (bool, bool, bool) {
    let (open, dirty, jis) = cells();
    open.store(initial_open, Ordering::Relaxed);
    apply_mode_key(
        vk,
        true,
        &ModeCells {
            open: &open,
            dirty: &dirty,
            jis_seen: &jis,
        },
    );
    (
        open.load(Ordering::Relaxed),
        dirty.load(Ordering::Relaxed),
        jis.load(Ordering::Relaxed),
    )
}

#[test]
fn letters_digits_and_editing_keys_map_to_windows_codes() {
    assert_eq!(keymap::to_vk(0x00), 0x41); // A
    assert_eq!(keymap::to_vk(0x06), 0x5A); // Z
    assert_eq!(keymap::to_vk(0x12), 0x31); // 1
    assert_eq!(keymap::to_vk(0x33), 0x08); // Backspace
    assert_eq!(keymap::to_vk(0x75), 0x2E); // Forward delete
    assert_eq!(keymap::to_vk(0x24), 0x0D); // Return
    assert_eq!(keymap::to_vk(0x31), 0x20); // Space
    assert_eq!(keymap::to_vk(0x35), 0x1B); // Escape
    assert_eq!(keymap::to_vk(0x7B), 0x25); // Left
}

#[test]
fn the_two_jis_mode_keys_map_to_the_alphanumeric_and_hiragana_codes() {
    assert_eq!(keymap::to_vk(0x66), 0xF0);
    assert_eq!(keymap::to_vk(0x68), 0xF2);
}

#[test]
fn no_key_maps_to_the_windows_toggle_code() {
    for mac_vk in 0u64..=0xFF {
        assert_ne!(keymap::to_vk(mac_vk), 0x19, "{mac_vk:#x}");
    }
}

#[test]
fn observed_keys_are_text_editing_escape_arrows_and_function_keys() {
    for mac_vk in [0x00u64, 0x06, 0x24, 0x31, 0x33, 0x75, 0x35, 0x7B, 0x7E, 0x7A, 0x6F] {
        assert!(keymap::observed(mac_vk), "{mac_vk:#x}");
    }
}

#[test]
fn mode_keys_modifiers_and_keypad_keys_are_not_observed() {
    // 0x66 and 0x68 are the JIS mode keys, 0x37 and 0x38 are Command and Shift, 0x52 is keypad 0.
    for mac_vk in [0x66u64, 0x68, 0x37, 0x38, 0x52, 0x0A] {
        assert!(!keymap::observed(mac_vk), "{mac_vk:#x}");
    }
}

#[test]
fn an_alphanumeric_key_press_closes_the_flag_and_marks_it_fresh() {
    assert_eq!(press_release(0xF0, true), (false, true, true));
}

#[test]
fn a_hiragana_key_press_opens_the_flag_and_marks_it_fresh() {
    assert_eq!(press_release(0xF2, false), (true, true, true));
}

#[test]
fn the_toggle_code_flips_the_flag_in_one_step() {
    assert_eq!(press_release(0x19, false), (true, true, true));
    assert_eq!(press_release(0x19, true), (false, true, true));
}

#[test]
fn an_ordinary_key_leaves_the_mode_cells_alone() {
    assert_eq!(press_release(0x41, true), (true, false, false));
}

#[test]
fn a_release_of_a_mode_key_sets_the_flag_without_marking_a_keyboard() {
    let (open, dirty, jis) = cells();
    apply_mode_key(
        0xF2,
        false,
        &ModeCells {
            open: &open,
            dirty: &dirty,
            jis_seen: &jis,
        },
    );
    assert!(open.load(Ordering::Relaxed));
    assert!(dirty.load(Ordering::Relaxed));
    assert!(!jis.load(Ordering::Relaxed));
}

#[test]
fn a_release_of_an_ordinary_key_changes_nothing() {
    let (open, dirty, jis) = cells();
    apply_mode_key(
        0x41,
        false,
        &ModeCells {
            open: &open,
            dirty: &dirty,
            jis_seen: &jis,
        },
    );
    assert!(!open.load(Ordering::Relaxed));
    assert!(!dirty.load(Ordering::Relaxed));
    assert!(!jis.load(Ordering::Relaxed));
}

#[test]
fn command_and_control_make_a_chord_and_shift_or_option_do_not() {
    assert_eq!(chord_of(true, false), Chord::Modified);
    assert_eq!(chord_of(false, true), Chord::Modified);
    assert_eq!(chord_of(true, true), Chord::Modified);
    assert_eq!(chord_of(false, false), Chord::Plain);
}

#[test]
fn a_key_event_is_queued_while_the_queue_has_room() {
    let (tx, rx) = sync_channel::<KeyEvent>(1);
    let dropped = AtomicU64::new(0);
    send_or_count_drop(&tx, KeyEvent { t: 1, vk: 0x41, press: true }, &dropped);
    assert_eq!(dropped.load(Ordering::Relaxed), 0);
    assert_eq!(rx.try_recv().expect("event was queued").vk, 0x41);
}

#[test]
fn a_key_event_is_dropped_and_counted_once_the_queue_is_full() {
    let (tx, _rx) = sync_channel::<KeyEvent>(1);
    let dropped = AtomicU64::new(0);
    send_or_count_drop(&tx, KeyEvent { t: 1, vk: 0x41, press: true }, &dropped);
    send_or_count_drop(&tx, KeyEvent { t: 2, vk: 0x42, press: true }, &dropped);
    send_or_count_drop(&tx, KeyEvent { t: 3, vk: 0x43, press: true }, &dropped);
    assert_eq!(dropped.load(Ordering::Relaxed), 2);
}

#[test]
fn the_mode_flag_is_trusted_when_a_mode_key_was_seen() {
    assert!(ime_source::resolve_open(true, false, true, Some(false)));
    assert!(!ime_source::resolve_open(false, true, false, Some(true)));
}

#[test]
fn the_queried_source_decides_only_when_no_mode_key_was_seen() {
    assert!(ime_source::resolve_open(false, false, false, Some(true)));
    assert!(!ime_source::resolve_open(false, false, true, Some(false)));
}

#[test]
fn the_flag_is_kept_when_no_query_has_completed() {
    assert!(ime_source::resolve_open(false, false, true, None));
    assert!(!ime_source::resolve_open(false, false, false, None));
}

#[test]
fn only_identifiers_of_a_japanese_input_method_are_recognised() {
    assert!(ime_source::is_japanese_source_id("com.apple.inputmethod.Japanese.Hiragana"));
    assert!(ime_source::is_japanese_source_id("com.apple.inputmethod.Japanese"));
    assert!(!ime_source::is_japanese_source_id("com.apple.keylayout.US"));
    assert!(!ime_source::is_japanese_source_id(""));
}

#[test]
fn auto_repeat_events_are_not_queued_and_only_observed_keys_are() {
    assert!(queue_row(false, 0x00));
    assert!(!queue_row(true, 0x00));
    assert!(!queue_row(false, 0x66));
    assert!(!queue_row(false, 0x37));
}

#[test]
fn every_press_and_any_mode_key_transition_wakes_the_poll() {
    assert!(wakes_poll(true, 0x41));
    assert!(!wakes_poll(false, 0x41));
    assert!(wakes_poll(false, 0xF2));
    assert!(wakes_poll(false, 0xF0));
}

#[test]
fn only_presses_drive_the_conversion_state_machine() {
    assert!(drives_compose(true));
    assert!(!drives_compose(false));
}

#[test]
fn the_poll_result_does_not_overwrite_a_mode_key_applied_in_the_meantime() {
    let open = AtomicBool::new(false);
    // The poll read `false`, then a mode key set the flag.
    open.store(true, Ordering::Release);
    assert!(!ime_source::publish_open(&open, false, false));
    assert!(open.load(Ordering::Acquire));
    open.store(false, Ordering::Release);
    assert!(ime_source::publish_open(&open, false, true));
    assert!(open.load(Ordering::Acquire));
}
