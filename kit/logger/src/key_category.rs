//! `KeyCategory`: folds a virtual-key code into one of fourteen key kinds.
//!
//! Everything between the `// @excerpt-begin key_category` and `// @excerpt-end key_category` markers
//! is pinned to a recorded SHA-256 (the `key_category` entry of `manifest.json`, checked by
//! `check_manifest.py --self`). The excerpted range also carries `ABSOLUTE_TIME_FIELDS`, which this
//! logger does not use, because the excerpt keeps the enclosing file's own layout rather than being
//! trimmed to only what is used here.
// @excerpt-begin key_category
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyCategory {
    Alphabetic,
    Numeric,
    Punctuation,
    SentencePunctuation,
    Tab,
    Backspace,
    Delete,
    Space,
    Enter,
    Navigation,
    Modifier,
    Function,
    Escape,
    Other,
}

impl KeyCategory {
    pub fn from_vk(vk: u32) -> Self {
        match vk {
            0x41..=0x5A => Self::Alphabetic,
            0x30..=0x39 | 0x60..=0x69 => Self::Numeric,
            0xBC | 0xBE => Self::SentencePunctuation,
            0x6A..=0x6F | 0xBA..=0xC0 | 0xDB..=0xDF | 0xE2 => Self::Punctuation,
            0x09 => Self::Tab,
            0x08 => Self::Backspace,
            0x2E => Self::Delete,
            0x20 => Self::Space,
            0x0D => Self::Enter,
            0x21..=0x28 => Self::Navigation,
            0x10 | 0x11 | 0x12 | 0x5B | 0x5C | 0xA0..=0xA5 => Self::Modifier,
            0x1B => Self::Escape,
            0x70..=0x7B => Self::Function,
            _ => Self::Other,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Alphabetic => "alphabetic",
            Self::Numeric => "numeric",
            Self::Punctuation => "punctuation",
            Self::SentencePunctuation => "sentence_punctuation",
            Self::Tab => "tab",
            Self::Backspace => "backspace",
            Self::Delete => "delete",
            Self::Space => "space",
            Self::Enter => "enter",
            Self::Navigation => "navigation",
            Self::Modifier => "modifier",
            Self::Function => "function",
            Self::Escape => "escape",
            Self::Other => "other",
        }
    }
}

const ABSOLUTE_TIME_FIELDS: &[&str] = &[
    "t",
    "ts",
    "session_start",
    "session_end",
    "scheduled_ts",
    "analysis_window_start_ms",
    "analysis_window_end_ms",
    "recovered_at",
];

// @excerpt-end key_category
