//! GitHub timestamps (`2026-11-21T01:02:03Z`) as Unix seconds.

/// Unix seconds of a UTC timestamp, or `None` when it has another form.
pub fn epoch(text: &str) -> Option<i64> {
    let b = text.as_bytes();
    if b.len() < 19 || !text.is_char_boundary(19) {
        return None;
    }
    let num = |from: usize, to: usize| -> Option<i64> { text.get(from..to)?.parse::<i64>().ok() };
    let (y, m, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let (hh, mm, ss) = (num(11, 13)?, num(14, 16)?, num(17, 19)?);
    if b[4] != b'-' || b[7] != b'-' || !(b[10] == b'T' || b[10] == b' ') {
        return None;
    }
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) || hh > 23 || mm > 59 || ss > 60 {
        return None;
    }
    // Days from civil (Howard Hinnant).
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + hh * 3600 + mm * 60 + ss)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_timestamp_is_unix_seconds() {
        assert_eq!(epoch("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(epoch("2026-11-21T00:00:00Z"), Some(1_795_219_200));
        assert_eq!(epoch("2024-02-29T12:00:01Z"), Some(1_709_208_001));
    }

    #[test]
    fn a_bad_timestamp_is_none() {
        assert_eq!(epoch(""), None);
        assert_eq!(epoch("yesterday"), None);
        assert_eq!(epoch("2026-13-01T00:00:00Z"), None);
        assert_eq!(epoch("2026/11/21T00:00:00Z"), None);
        assert_eq!(epoch("2026-11-21T25:00:00Z"), None);
    }
}
