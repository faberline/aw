use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::work::ports::Clock;

pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_secs() as i64)
    }
}
