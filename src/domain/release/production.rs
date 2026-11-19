//! Production acceptance of an app (`aw release-receipt --production-acceptance`).
//!
//! The port of `production_receipt.py` of sift: from the version in
//! `production_since` on, one acceptance file binds twelve production gates to
//! the candidate commit and image, and each gate has a raw evidence file next
//! to it. The gate names, the fields and the limits are the contract of sift
//! 0.2.0. This port does not change them.
//!
//! The rules are pure. Files come through [`ReceiptFiles`]. A refusal reads
//! `production receipt refused: <reason>`.

use std::path::Path;

use regex::Regex;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use super::receipt::ReceiptFiles;

/// The gates, in the order of the Python tuple.
pub const GATES: [&str; 12] = [
    "functional",
    "promql_oracle",
    "control_failover",
    "load_30m",
    "load_24h",
    "node_failure",
    "pvc_restore",
    "gcs_outage",
    "all_disk_restore",
    "source_fidelity",
    "capacity",
    "delivery",
];

const SOURCE_SIGNALS: [&str; 4] = ["logs", "metrics", "traces", "alerts"];
const COMMON: [&str; 5] = [
    "result",
    "source_commit",
    "image",
    "cluster_identity_sha256",
    "evidence_sha256",
];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Str,
    Int,
    Float,
    Bool,
    List,
    Dict,
}

use Kind::{Bool, Dict, Float, Int, List, Str};

const LOAD: [(&str, Kind); 11] = [
    ("duration_seconds", Int),
    ("acknowledged_events", Int),
    ("offered_events_per_second", Int),
    ("concurrent_queries", Bool),
    ("concurrent_alerts", Bool),
    ("log_query_p95_seconds", Float),
    ("metric_query_p95_seconds", Float),
    ("trace_read_p95_seconds", Float),
    ("content_differences", Int),
    ("identity_differences", Int),
    ("incomplete_queries", Int),
];

fn fields(gate: &str) -> &'static [(&'static str, Kind)] {
    match gate {
        "promql_oracle" => &[("prometheus_version", Str), ("expressions_compared", Int)],
        "control_failover" => &[("control_state_differences", Int), ("leader_changes", Int)],
        "node_failure" => &[("lost_acknowledged_events", Int)],
        "pvc_restore" => &[
            ("lost_acknowledged_events", Int),
            ("control_state_differences", Int),
        ],
        "gcs_outage" => &[
            ("duration_seconds", Int),
            ("acknowledged_after_rpo_limit", Int),
            ("collector_pending_preserved", Bool),
            ("resumed", Bool),
        ],
        "all_disk_restore" => &[
            ("control_state_differences", Int),
            ("lost_recent_seconds", Int),
            ("identity_differences", Int),
        ],
        "source_fidelity" => &[
            ("duration_seconds", Int),
            ("reference_origin", Str),
            ("differences", Int),
            ("compared_signals", List),
            ("compared_records", Dict),
            ("complete", Bool),
            ("gcp_rollback_configured", Bool),
        ],
        "capacity" => &[
            ("measured_samples", Int),
            ("sample_mean_bytes", Float),
            ("cost_cap_usd", Float),
            ("actual_cost_usd", Float),
            ("capacity_exceeded", Bool),
            ("pvc_sized_from_samples", Bool),
        ],
        "delivery" => &[
            ("webhook_p95_seconds", Float),
            ("retry_notification_ids_unchanged", Bool),
            ("unknown_recovery_notifications", Int),
            ("mute_verified", Bool),
            ("hold_verified", Bool),
        ],
        "load_30m" | "load_24h" => &LOAD,
        _ => &[],
    }
}

/// The proof, its SHA-256 and the public part of the receipt.
pub struct Proof {
    pub value: Value,
    pub hash: String,
}

type Checked<T> = Result<T, String>;

fn refuse<T>(message: impl AsRef<str>) -> Checked<T> {
    Err(format!("production receipt refused: {}", message.as_ref()))
}

fn require(condition: bool, message: &str) -> Checked<()> {
    if condition { Ok(()) } else { refuse(message) }
}

fn sha256(raw: &[u8]) -> String {
    Sha256::digest(raw)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn digest(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|s| {
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// `version >= since`, both as `x.y.z`.
pub fn at_least(version: &str, since: &str) -> Option<bool> {
    let parse = |text: &str| -> Option<Vec<u64>> {
        text.split('.').map(|p| p.parse::<u64>().ok()).collect()
    };
    Some(parse(version)? >= parse(since)?)
}

fn exact_keys(value: &Value, keys: &[&str], name: &str) -> Checked<()> {
    let ok = value
        .as_object()
        .is_some_and(|m| m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k)));
    require(ok, &format!("{name} fields differ"))
}

fn typed(value: &Value, kind: Kind, name: &str) -> Checked<()> {
    match kind {
        Float => require(
            value.is_number() && value.as_f64().is_some_and(f64::is_finite),
            &format!("{name} must be finite numeric data"),
        ),
        _ => {
            let ok = match kind {
                Str => value.is_string(),
                Int => value.is_i64() || value.is_u64(),
                Bool => value.is_boolean(),
                List => value.is_array(),
                Dict => value.is_object(),
                Float => false,
            };
            require(ok, &format!("{name} has the wrong type"))
        }
    }
}

fn int(value: &Value, key: &str) -> i128 {
    let v = &value[key];
    v.as_i64()
        .map(i128::from)
        .or_else(|| v.as_u64().map(i128::from))
        .unwrap_or(0)
}

fn num(value: &Value, key: &str, default: f64) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or(default)
}

fn is(value: &Value, key: &str, expected: bool) -> bool {
    value.get(key) == Some(&Value::Bool(expected))
}

/// The rules of the acceptance file. `image` is the candidate image.
pub fn validate(
    proof: &Value,
    candidate: &Value,
    image: &str,
    app: &str,
    owner: &str,
) -> Checked<()> {
    exact_keys(
        proof,
        &[
            "schema",
            "source_commit",
            "image",
            "topology",
            "cluster_identity_sha256",
            "gates",
            "cleanup",
        ],
        "acceptance",
    )?;
    let commit = candidate["commit"].as_str().unwrap_or_default();
    require(
        Regex::new("^[0-9a-f]{40}$")
            .expect("fixed")
            .is_match(commit),
        "candidate source format",
    )?;
    let pattern = format!(
        "^{}@sha256:[0-9a-f]{{64}}$",
        regex::escape(&format!("{owner}/{app}"))
    );
    require(
        Regex::new(&pattern).expect("fixed").is_match(image),
        "immutable image format",
    )?;
    exact_keys(&proof["cleanup"], &["status"], "cleanup")?;
    let topology = &proof["topology"];
    exact_keys(
        topology,
        &["gke_mode", "store_voters", "control_voters"],
        "topology",
    )?;
    typed(&topology["store_voters"], Int, "store voters")?;
    typed(&topology["control_voters"], Int, "control voters")?;
    require(
        proof["schema"] == "sift.production-acceptance/v2",
        "acceptance schema",
    )?;
    require(proof["source_commit"] == commit, "source commit differs")?;
    require(proof["image"] == image, "candidate image differs")?;
    require(
        topology["gke_mode"] == "standard"
            && int(topology, "store_voters") == 3
            && int(topology, "control_voters") == 3,
        "fixed Standard GKE topology",
    )?;
    require(
        digest(proof.get("cluster_identity_sha256")),
        "cluster identity binding",
    )?;
    let gates = proof["gates"].as_object();
    require(
        gates.is_some_and(|g| g.len() == GATES.len() && GATES.iter().all(|n| g.contains_key(*n))),
        "every production gate is required",
    )?;
    for name in GATES {
        let gate = &proof["gates"][name];
        let mut keys: Vec<&str> = COMMON.to_vec();
        keys.extend(fields(name).iter().map(|(k, _)| *k));
        exact_keys(gate, &keys, name)?;
        for (field, kind) in fields(name) {
            typed(&gate[*field], *kind, &format!("{name}.{field}"))?;
        }
        require(
            gate["result"] == "passed",
            &format!("{name} has not passed"),
        )?;
        require(
            gate["source_commit"] == commit && gate["image"] == image,
            &format!("{name} candidate differs"),
        )?;
        require(
            gate["cluster_identity_sha256"] == proof["cluster_identity_sha256"],
            &format!("{name} cluster differs"),
        )?;
        require(
            digest(gate.get("evidence_sha256")),
            &format!("{name} evidence hash"),
        )?;
    }
    for (name, seconds, events) in [
        ("load_30m", 1800_i128, 18_000_000_i128),
        ("load_24h", 86_400, 864_000_000),
    ] {
        let g = &proof["gates"][name];
        require(
            int(g, "duration_seconds") >= seconds && int(g, "acknowledged_events") >= events,
            &format!("{name} duration or count"),
        )?;
        require(
            int(g, "offered_events_per_second") == 10_000
                && int(g, "acknowledged_events") >= int(g, "duration_seconds") * 10_000,
            &format!("{name} sustained throughput"),
        )?;
        require(
            is(g, "concurrent_queries", true) && is(g, "concurrent_alerts", true),
            &format!("{name} concurrent work"),
        )?;
        let log = num(g, "log_query_p95_seconds", 999.0);
        let metric = num(g, "metric_query_p95_seconds", 999.0);
        require(
            0.0 < log && log <= 2.0 && 0.0 < metric && metric <= 2.0,
            &format!("{name} log or metric latency"),
        )?;
        let trace = num(g, "trace_read_p95_seconds", 999.0);
        require(
            0.0 < trace && trace <= 1.0,
            &format!("{name} trace latency"),
        )?;
        require(
            int(g, "content_differences") == 0 && int(g, "identity_differences") == 0,
            &format!("{name} exact data comparison"),
        )?;
        require(
            int(g, "incomplete_queries") == 0,
            &format!("{name} incomplete queries"),
        )?;
    }
    for name in ["node_failure", "pvc_restore"] {
        require(
            int(&proof["gates"][name], "lost_acknowledged_events") == 0,
            &format!("{name} acknowledged data loss"),
        )?;
    }
    for name in ["control_failover", "pvc_restore", "all_disk_restore"] {
        require(
            int(&proof["gates"][name], "control_state_differences") == 0,
            &format!("{name} control mismatch"),
        )?;
    }
    require(
        int(&proof["gates"]["control_failover"], "leader_changes") >= 1,
        "no control leader change",
    )?;
    let g = &proof["gates"]["gcs_outage"];
    require(
        int(g, "duration_seconds") > 900 && int(g, "acknowledged_after_rpo_limit") == 0,
        "GCS outage must stop acknowledgements",
    )?;
    require(
        is(g, "collector_pending_preserved", true) && is(g, "resumed", true),
        "GCS outage collection or recovery",
    )?;
    let g = &proof["gates"]["all_disk_restore"];
    let lost = int(g, "lost_recent_seconds");
    require(
        (0..=900).contains(&lost) && int(g, "identity_differences") == 0,
        "all-disk restore bound or identity",
    )?;
    let g = &proof["gates"]["source_fidelity"];
    require(
        int(g, "duration_seconds") >= 1800 && int(g, "differences") == 0,
        "30-minute source comparison",
    )?;
    let origin = g["reference_origin"].as_str().unwrap_or_default();
    require(
        origin == "generated" || origin == "gcp",
        "source reference origin",
    )?;
    let signals: Vec<&str> = g["compared_signals"]
        .as_array()
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    require(
        signals == SOURCE_SIGNALS
            && g["compared_signals"].as_array().map(Vec::len) == Some(SOURCE_SIGNALS.len())
            && is(g, "complete", true),
        "complete source coverage",
    )?;
    exact_keys(
        &g["compared_records"],
        &SOURCE_SIGNALS,
        "compared source counts",
    )?;
    let counted = g["compared_records"].as_object().is_some_and(|m| {
        m.values()
            .all(|c| (c.is_i64() || c.is_u64()) && c.as_f64().is_some_and(|n| n > 0.0))
    });
    require(counted, "each signal needs compared records")?;
    require(
        is(g, "gcp_rollback_configured", origin == "gcp"),
        "migration rollback or fresh-install reference",
    )?;
    let g = &proof["gates"]["capacity"];
    let cap = num(g, "cost_cap_usd", 0.0);
    let actual = num(g, "actual_cost_usd", -1.0);
    require(
        int(g, "measured_samples") > 0 && num(g, "sample_mean_bytes", 0.0) > 0.0,
        "real sample measurement",
    )?;
    require(cap > 0.0 && 0.0 <= actual && actual <= cap, "cost cap")?;
    require(
        is(g, "capacity_exceeded", false) && is(g, "pvc_sized_from_samples", true),
        "capacity or PVC sizing",
    )?;
    let g = &proof["gates"]["delivery"];
    let webhook = num(g, "webhook_p95_seconds", 999.0);
    require(
        0.0 < webhook && webhook <= 60.0 && is(g, "retry_notification_ids_unchanged", true),
        "notification latency or identity",
    )?;
    require(
        int(g, "unknown_recovery_notifications") == 0
            && is(g, "mute_verified", true)
            && is(g, "hold_verified", true),
        "notification state semantics",
    )?;
    let g = &proof["gates"]["promql_oracle"];
    require(
        g["prometheus_version"] == "3.5.0" && int(g, "expressions_compared") >= 22,
        "pinned Prometheus oracle",
    )?;
    require(proof["cleanup"]["status"] == "clean", "acceptance cleanup")
}

/// Equal as Python `==` on parsed JSON: `1` equals `1.0`.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x == y || x.as_f64() == y.as_f64(),
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| same(p, q))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
        }
        _ => a == b,
    }
}

/// Read the acceptance file and the raw evidence file of each gate.
pub fn load_proof(
    files: &dyn ReceiptFiles,
    path: Option<&Path>,
    candidate: &Value,
    image: &str,
    app: &str,
    owner: &str,
) -> Checked<Proof> {
    let Some(path) = path else {
        return refuse("--production-acceptance is required for 0.2.0");
    };
    let raw = files.regular(path);
    let Ok(raw) = raw else {
        return refuse("regular acceptance file");
    };
    require(!raw.is_empty() && raw.len() <= 32_768, "acceptance size")?;
    let Ok(proof) = serde_json::from_slice::<Value>(&raw) else {
        return refuse("acceptance is not valid JSON");
    };
    validate(&proof, candidate, image, app, owner)?;
    let folder = path
        .parent()
        .unwrap_or_else(|| Path::new(""))
        .join("evidence");
    for name in GATES {
        let gate = &proof["gates"][name];
        let Ok(bytes) = files.regular(&folder.join(format!("{name}.json"))) else {
            return refuse(format!("{name} evidence file is missing"));
        };
        require(
            !bytes.is_empty() && bytes.len() <= 1_048_576,
            &format!("{name} evidence size"),
        )?;
        require(
            Some(sha256(&bytes).as_str()) == gate["evidence_sha256"].as_str(),
            &format!("{name} evidence bytes differ"),
        )?;
        let Ok(evidence) = serde_json::from_slice::<Value>(&bytes) else {
            return refuse(format!("{name} evidence is not valid JSON"));
        };
        let expected: Map<String, Value> = gate
            .as_object()
            .map(|m| {
                m.iter()
                    .filter(|(k, _)| k.as_str() != "evidence_sha256")
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect()
            })
            .unwrap_or_default();
        require(
            same(&evidence, &Value::Object(expected)),
            &format!("{name} evidence data differs"),
        )?;
    }
    Ok(Proof {
        hash: sha256(&raw),
        value: proof,
    })
}
