// The checksum of this file is pinned by the manifests in kit/logger (manifest*.json).

pub fn is_feature_key(vk_code: u32) -> bool {
    matches!(
        vk_code,
        0x41..=0x5A
        | 0x30..=0x39
        | 0x60..=0x6F
        | 0xBA..=0xC0
        | 0xDB..=0xDF
        | 0x08
        | 0x0D
        | 0x20
        | 0x09
        | 0x2E
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feature_set_is_the_frozen_typing_key_set() {
        for (vk, why) in [
            (0x41, "A"),
            (0x5A, "Z"),
            (0x30, "0"),
            (0x39, "9"),
            (0x60, "Numpad 0 (lower bound)"),
            (0x6F, "Numpad Divide (upper bound)"),
            (0xBA, "OEM symbols, first group (lower bound)"),
            (0xC0, "OEM symbols, first group (upper bound)"),
            (0xDB, "OEM symbols, second group (lower bound)"),
            (0xDF, "OEM symbols, second group (upper bound)"),
            (0x08, "Backspace"),
            (0x0D, "Enter"),
            (0x20, "Space"),
            (0x09, "Tab"),
            (0x2E, "Delete"),
        ] {
            assert!(is_feature_key(vk), "{why} is a feature key");
        }
        for (vk, why) in [
            (0x40, "just before A-Z"),
            (0x5B, "just after A-Z (VK_LWIN)"),
            (0x2F, "just before 0-9"),
            (0x3A, "just after 0-9"),
            (0x5F, "just before Numpad"),
            (0x70, "just after Numpad (VK_F1)"),
            (0xB9, "just before the first OEM group"),
            (0xC1, "just after the first OEM group"),
            (0xDA, "just before the second OEM group"),
            (0xE0, "just after the second OEM group"),
        ] {
            assert!(!is_feature_key(vk), "{why} is not a feature key");
        }
    }

    #[test]
    fn observed_only_keys_never_reach_the_feature_path() {
        for (vk, why) in [
            (0x10, "VK_SHIFT"),
            (0x11, "VK_CONTROL"),
            (0x12, "VK_MENU"),
            (0x5B, "VK_LWIN"),
            (0x5C, "VK_RWIN"),
            (0xA0, "VK_LSHIFT (the form returned by the low-level hook)"),
            (0xA1, "VK_RSHIFT"),
            (0xA2, "VK_LCONTROL"),
            (0xA3, "VK_RCONTROL"),
            (0xA4, "VK_LMENU"),
            (0xA5, "VK_RMENU"),
            (0x21, "VK_PRIOR (Navigation lower bound)"),
            (0x25, "VK_LEFT"),
            (0x28, "VK_DOWN (Navigation upper bound)"),
            (0x1B, "VK_ESCAPE"),
            (0x70, "VK_F1"),
            (0x7B, "VK_F12"),
            (0x07, "representative VK of `other` in replay"),
        ] {
            assert!(!is_feature_key(vk), "{why} flows into the features");
        }
    }

    #[test]
    fn macos_numpad_cannot_be_observed_because_both_mappings_land_inside_the_feature_set() {
        for (mac_keycode, collides_with) in [
            (0x52u32, "VK_R (Latin letter)"),
            (0x53, "VK_S (Latin letter)"),
            (0x41, "VK_A (Latin letter)"),
            (0x4B, "VK_K (Latin letter)"),
        ] {
            assert!(
                is_feature_key(mac_keycode),
                "The macOS keypad key {mac_keycode:#04X} passes through, collides with {collides_with} and enters the features"
            );
        }
        for vk in [0x60u32, 0x6F] {
            assert!(
                is_feature_key(vk),
                "The Windows keypad VK {vk:#04X} is inside the feature set"
            );
        }
    }

    #[test]
    fn replay_representative_vks_of_new_categories_are_not_feature_keys() {
        for (category, vk) in [
            ("navigation", 0x25u32),
            ("modifier", 0x10),
            ("function", 0x70),
            ("escape", 0x1B),
            ("other", 0x07),
        ] {
            assert!(
                !is_feature_key(vk),
                "For category {category:?}, the representative VK {vk:#04X} flows into the features"
            );
        }
        for (category, vk) in [
            ("alphabetic", 0x41u32),
            ("numeric", 0x30),
            ("punctuation", 0xBA),
            ("sentence_punctuation", 0xBE),
            ("tab", 0x09),
            ("backspace", 0x08),
            ("delete", 0x2E),
            ("space", 0x20),
            ("enter", 0x0D),
        ] {
            assert!(
                is_feature_key(vk),
                "For category {category:?}, the representative VK {vk:#04X} dropped out of the features (a regression)"
            );
        }
    }
}
