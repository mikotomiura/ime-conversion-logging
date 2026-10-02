// The checksum of this file is pinned by the manifests in kit/logger (manifest*.json).

use super::hook::{
    VK_DBE_ALPHANUMERIC, VK_DBE_DBCSCHAR, VK_DBE_HIRAGANA, VK_DBE_KATAKANA, VK_DBE_SBCSCHAR,
    VK_KANJI,
};

#[derive(Debug, PartialEq, Eq)]
pub enum ImeOpenTransition {
    Set(bool),
    Toggle,
    None,
}

pub fn ime_open_after_press(vk_code: u32) -> ImeOpenTransition {
    match vk_code {
        VK_DBE_ALPHANUMERIC | VK_DBE_SBCSCHAR => ImeOpenTransition::Set(false),
        VK_DBE_KATAKANA | VK_DBE_HIRAGANA | VK_DBE_DBCSCHAR => ImeOpenTransition::Set(true),
        VK_KANJI => ImeOpenTransition::Toggle,
        _ => ImeOpenTransition::None,
    }
}

pub fn ime_open_after_release(vk_code: u32) -> Option<bool> {
    match vk_code {
        VK_DBE_KATAKANA | VK_DBE_HIRAGANA | VK_DBE_DBCSCHAR => Some(true),
        VK_DBE_ALPHANUMERIC | VK_DBE_SBCSCHAR => Some(false),
        _ => None,
    }
}

pub fn is_ime_mode_key(vk_code: u32) -> bool {
    matches!(
        vk_code,
        VK_DBE_ALPHANUMERIC
            | VK_DBE_KATAKANA
            | VK_DBE_HIRAGANA
            | VK_DBE_SBCSCHAR
            | VK_DBE_DBCSCHAR
            | VK_KANJI
    )
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn press_of_alphanumeric_keys_closes_ime() {
        assert_eq!(
            ime_open_after_press(VK_DBE_ALPHANUMERIC),
            ImeOpenTransition::Set(false)
        );
        assert_eq!(
            ime_open_after_press(VK_DBE_SBCSCHAR),
            ImeOpenTransition::Set(false)
        );
    }

    #[test]
    fn press_of_japanese_keys_opens_ime() {
        assert_eq!(
            ime_open_after_press(VK_DBE_HIRAGANA),
            ImeOpenTransition::Set(true)
        );
        assert_eq!(
            ime_open_after_press(VK_DBE_KATAKANA),
            ImeOpenTransition::Set(true)
        );
        assert_eq!(
            ime_open_after_press(VK_DBE_DBCSCHAR),
            ImeOpenTransition::Set(true)
        );
    }

    #[test]
    fn press_of_kanji_key_returns_toggle() {
        assert_eq!(ime_open_after_press(VK_KANJI), ImeOpenTransition::Toggle);
    }

    #[test]
    fn press_of_typing_key_does_not_change_ime_state() {
        assert_eq!(ime_open_after_press(0x41), ImeOpenTransition::None);
    }

    #[test]
    fn release_of_japanese_keys_opens_ime() {
        assert_eq!(ime_open_after_release(VK_DBE_HIRAGANA), Some(true));
        assert_eq!(ime_open_after_release(VK_DBE_KATAKANA), Some(true));
        assert_eq!(ime_open_after_release(VK_DBE_DBCSCHAR), Some(true));
    }

    #[test]
    fn release_of_alphanumeric_keys_closes_ime() {
        assert_eq!(ime_open_after_release(VK_DBE_ALPHANUMERIC), Some(false));
        assert_eq!(ime_open_after_release(VK_DBE_SBCSCHAR), Some(false));
    }

    #[test]
    fn release_of_kanji_key_does_not_change_ime_state() {
        assert_eq!(ime_open_after_release(VK_KANJI), None);
    }

    #[test]
    fn ime_mode_key_matches_all_dbe_and_kanji_keys() {
        for vk in [
            VK_DBE_ALPHANUMERIC,
            VK_DBE_KATAKANA,
            VK_DBE_HIRAGANA,
            VK_DBE_SBCSCHAR,
            VK_DBE_DBCSCHAR,
            VK_KANJI,
        ] {
            assert!(is_ime_mode_key(vk));
        }
    }

    #[test]
    fn ime_mode_key_excludes_typing_keys() {
        assert!(!is_ime_mode_key(0x41), "The A key is not an IME mode key.");
    }

    #[test]
    fn the_kana_key_is_idempotent_not_a_toggle() {
        let t = ime_open_after_press(VK_DBE_HIRAGANA);
        assert_eq!(t, ImeOpenTransition::Set(true));
        assert_ne!(
            t,
            ImeOpenTransition::Toggle,
            "Treating the Kana key as a toggle inverts IME_OPEN relative to the actual state after an even number of presses (measured: the conversions in that interval were missing entirely from ime_shadow)."
        );
    }
}
