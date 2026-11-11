//! The gates of the shared release promotion workflow,
//! `.github/workflows/service-release.yml`
//! (`docs/standards/release-tooling.md`). Each test fails when a gate that
//! the per-repository `<app>-release.yml` files carried goes missing.

use std::collections::BTreeSet;

use serde_norway::Value;

const TEXT: &str = include_str!("../../.github/workflows/service-release.yml");

fn workflow() -> Value {
    serde_norway::from_str(TEXT).unwrap()
}

fn keys(value: &Value) -> BTreeSet<String> {
    value
        .as_mapping()
        .unwrap()
        .keys()
        .map(|key| key.as_str().unwrap().to_string())
        .collect()
}

fn set(names: &[&str]) -> BTreeSet<String> {
    names.iter().map(|name| name.to_string()).collect()
}

/// Every `run:` script of the workflow, in file order.
fn run_scripts() -> Vec<String> {
    let workflow = workflow();
    let mut scripts = Vec::new();
    for job in ["verify-inputs", "publish-release"] {
        for step in workflow["jobs"][job]["steps"].as_sequence().unwrap() {
            if let Some(run) = step.get("run") {
                scripts.push(run.as_str().unwrap().to_string());
            }
        }
    }
    scripts
}

fn count(needle: &str) -> usize {
    run_scripts()
        .iter()
        .map(|script| script.matches(needle).count())
        .sum()
}

#[test]
fn the_only_trigger_is_workflow_call() {
    // serde reads the YAML 1.1 key `on` as a string here; accept `true` too.
    let workflow = workflow();
    let on = workflow
        .get("on")
        .or_else(|| workflow.get(Value::Bool(true)))
        .unwrap();
    assert_eq!(keys(on), set(&["workflow_call"]));
}

#[test]
fn the_inputs_are_the_reviewed_set() {
    let inputs = &workflow()["on"]["workflow_call"]["inputs"];
    assert_eq!(
        keys(inputs),
        set(&[
            "app",
            "version",
            "candidate_run_id",
            "candidate_run_attempt",
            "gke_receipt_b64",
            "gke_receipt_sha256",
            "gke_receipt_sidecar_b64",
            "gke_receipt_sidecar_sha256",
            "compatibility_note",
            "release_notes_extra",
            "image_note",
            "verify_script",
            "verify_takes_app",
        ])
    );
    for required in [
        "app",
        "version",
        "candidate_run_id",
        "candidate_run_attempt",
        "gke_receipt_b64",
        "gke_receipt_sha256",
        "gke_receipt_sidecar_b64",
        "gke_receipt_sidecar_sha256",
        "compatibility_note",
    ] {
        assert_eq!(
            inputs[required]["required"].as_bool(),
            Some(true),
            "{required}"
        );
    }
}

#[test]
fn the_jobs_are_verify_then_publish() {
    let workflow = workflow();
    let jobs = &workflow["jobs"];
    assert_eq!(keys(jobs), set(&["verify-inputs", "publish-release"]));
    assert_eq!(
        jobs["publish-release"]["needs"].as_str(),
        Some("verify-inputs")
    );
    assert!(workflow.get("permissions").is_none());
    assert!(workflow.get("concurrency").is_none());
}

#[test]
fn only_the_publish_job_can_write() {
    let jobs = &workflow()["jobs"];
    let verify = jobs["verify-inputs"]["permissions"].as_mapping().unwrap();
    assert_eq!(verify.len(), 5);
    for (scope, level) in verify {
        assert_eq!(level.as_str(), Some("read"), "{scope:?}");
    }
    let publish = &jobs["publish-release"]["permissions"];
    assert_eq!(
        keys(publish),
        set(&[
            "actions",
            "attestations",
            "contents",
            "packages",
            "pull-requests"
        ])
    );
    assert_eq!(publish["contents"].as_str(), Some("write"));
    assert_eq!(publish["packages"].as_str(), Some("write"));
    for scope in ["actions", "attestations", "pull-requests"] {
        assert_eq!(publish[scope].as_str(), Some("read"), "{scope}");
    }
}

#[test]
fn every_action_is_pinned_to_a_commit() {
    let mut seen = 0;
    for line in TEXT.lines() {
        let Some(action) = line.trim().strip_prefix("- uses: ") else {
            continue;
        };
        let reference = action.split(' ').next().unwrap();
        let (_, sha) = reference.rsplit_once('@').unwrap();
        assert!(
            sha.len() == 40 && sha.chars().all(|c| c.is_ascii_hexdigit()),
            "{action}"
        );
        seen += 1;
    }
    assert_eq!(seen, 8);
    assert!(!TEXT.contains("build-push-action"));
}

#[test]
fn promotion_never_builds() {
    for script in run_scripts() {
        let tokens: Vec<&str> = script.split_whitespace().collect();
        for forbidden in [
            &["docker", "build"][..],
            &["docker", "buildx", "build"],
            &["cargo", "build"],
            &["cargo", "zigbuild"],
            &["cross", "build"],
        ] {
            assert!(
                !tokens.windows(forbidden.len()).any(|w| w == forbidden),
                "{forbidden:?}"
            );
        }
        for token in tokens {
            assert!(
                !matches!(token, "eval" | "bash" | "sh" | "source"),
                "{token}"
            );
        }
    }
}

#[test]
fn expressions_never_enter_a_script() {
    // An expression is only ever the whole value of a key (env, with,
    // outputs). A script reads inputs from the environment.
    for script in run_scripts() {
        assert!(!script.contains("${{"), "{script}");
    }
    for line in TEXT
        .lines()
        .filter(|line| line.contains("${{") && !line.starts_with('#'))
    {
        let (key, value) = line.trim().split_once(": ").unwrap();
        assert!(
            key.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'),
            "{line}"
        );
        assert!(
            value.starts_with("${{ ") && value.ends_with(" }}"),
            "{line}"
        );
    }
}

#[test]
fn the_identity_gates_are_present() {
    let script = &run_scripts()[0];
    for gate in [
        r#"[[ "$APP" =~ ^[a-z][a-z0-9-]*$ ]]"#,
        r#"[[ "$GITHUB_REPOSITORY" == "faberline/$APP" ]]"#,
        r#"[[ "$GITHUB_EVENT_NAME" == workflow_dispatch ]]"#,
        r#"[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]"#,
        r#"[[ "$GITHUB_REF" == "refs/tags/${APP}@${VERSION}" ]]"#,
        r#"[[ "$GITHUB_WORKFLOW_REF" == "${GITHUB_REPOSITORY}/.github/workflows/${APP}-release.yml@${GITHUB_REF}" ]]"#,
        r#"[[ -f "$VERIFY_SCRIPT" && ! -L "$VERIFY_SCRIPT" && -x "$VERIFY_SCRIPT" ]]"#,
        r#"[[ "$COMPATIBILITY_NOTE" == "- Compatibility: "?* && "$COMPATIBILITY_NOTE" != *$'\n'* ]]"#,
    ] {
        assert!(script.contains(&format!("{gate} || {{ echo ")), "{gate}");
    }
    for script in run_scripts() {
        assert!(script.starts_with("set -euo pipefail\n"));
    }
}

#[test]
fn both_jobs_bind_the_receipt_bytes() {
    for gate in [
        r#"[[ "$encoded" =~ ^[A-Za-z0-9+/]+={0,2}$ && $((${#encoded} % 4)) -eq 0 ]] || {"#,
        r#"[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {"#,
        r#"[[ "$actual" == "$expected" ]] || {"#,
        r#"decode_exact "$GKE_RECEIPT_B64" "$GKE_RECEIPT_SHA256" "gke-receipt/${APP}-gke-receipt.json""#,
        r#"decode_exact "$GKE_RECEIPT_SIDECAR_B64" "$GKE_RECEIPT_SIDECAR_SHA256" "gke-receipt/${APP}-gke-receipt.json.sha256""#,
    ] {
        assert_eq!(count(gate), 2, "{gate}");
    }
}

#[test]
fn the_verifier_runs_four_times_with_the_exact_attempt() {
    assert_eq!(count(r#""$VERIFY_SCRIPT" "${app_args[@]}" \"#), 4);
    assert_eq!(
        count(r#"--candidate-run-attempt "$CANDIDATE_RUN_ATTEMPT" \"#),
        4
    );
    assert_eq!(count(r#"--candidate-run-id "$CANDIDATE_RUN_ID" \"#), 4);
    assert_eq!(count(r#"--commit "$GITHUB_SHA" \"#), 4);
    assert_eq!(count(r#"--tag "${APP}@${VERSION}" \"#), 4);
    assert_eq!(count(r#"--gke-receipt "$GKE_RECEIPT" \"#), 4);
    assert_eq!(
        count(r#"--gke-receipt-sidecar "$GKE_RECEIPT_SIDECAR" \"#),
        4
    );
    assert_eq!(count("--mode candidate"), 2);
    assert_eq!(count("--mode public"), 2);
    // Candidate mode comes before any write; public mode is the last step.
    let scripts = run_scripts();
    assert!(scripts.last().unwrap().contains("--mode public"));
}

#[test]
fn the_contract_is_checked_in_both_jobs() {
    for gate in [
        ".commit == $commit and .candidate_run_id == $run and",
        ".candidate_attempt == $attempt and",
        "\n  .gke_receipt_sha256 == $receipt and\n",
        "\n  .gke_receipt_sidecar_sha256 == $sidecar and\n",
        r#"([.root_digest,.amd64_digest,.arm64_digest] | all(test("^sha256:[0-9a-f]{64}$")))"#,
        "'.root_digest == $root and .amd64_digest == $amd64 and .arm64_digest == $arm64 and .gke_receipt_sha256 == $receipt and .gke_receipt_sidecar_sha256 == $sidecar'",
    ] {
        assert_eq!(count(gate), 1, "{gate}");
    }
}

#[test]
fn image_tags_move_only_to_the_candidate_root() {
    let creates: Vec<String> = run_scripts()
        .iter()
        .flat_map(|script| script.lines().map(str::to_string).collect::<Vec<_>>())
        .filter(|line| line.contains("docker buildx imagetools create"))
        .collect();
    assert_eq!(creates.len(), 2);
    for line in &creates {
        assert!(line.trim().ends_with(r#""$root_ref""#), "{line}");
    }
    for gate in [
        r#"root_ref="${image_repo}@${root_digest}""#,
        r#"[[ -z "$semver_current" || "$semver_current" == "$root_digest" ]] || {"#,
        r#"echo "cannot prove GHCR image state: $ref" >&2"#,
        r#"echo "newer published semver image is absent: $next" >&2"#,
        r#"echo "latest does not match a newer published semver root" >&2"#,
        r#"echo "latest does not match an older published semver root" >&2"#,
        r#"echo "semver tag did not bind candidate root" >&2"#,
        r#"[[ "$(digest_or_absent "${image_repo}:latest")" == "$latest_current" ]] || { echo "latest changed before write" >&2; exit 1; }"#,
        r#"echo "latest tag did not bind candidate root" >&2"#,
    ] {
        assert_eq!(count(gate), 1, "{gate}");
    }
}

#[test]
fn the_release_carries_the_candidate_bytes_and_evidence() {
    for gate in [
        r#"--name "${APP}-release-candidate-${CANDIDATE_RUN_ID}-${CANDIDATE_ATTEMPT}""#,
        "[[ -f candidate/final-candidate-manifest.json && -f candidate/final-candidate-manifest.json.sha256 ]] || {",
        r#"candidate/"${APP}"-*.tar.gz candidate/"${APP}"-*.tar.gz.sha256 candidate/spdx-amd64.json candidate/spdx-arm64.json "gke-receipt/${APP}-gke-receipt.json" "gke-receipt/${APP}-gke-receipt.json.sha256""#,
        r#"gh release create "${APP}@${VERSION}" --repo "$GITHUB_REPOSITORY" --target "$GITHUB_SHA""#,
        "'- Source commit: %s\\n'",
        "'- Pull request: %s\\n'",
        "'- Candidate run: %s\\n'",
        "'- Promotion run: https://github.com/%s/actions/runs/%s/attempts/%s\\n'",
        "'- Root index digest: %s\\n'",
        "'- linux/amd64 digest: %s\\n'",
        "'- linux/arm64 digest: %s\\n'",
        "'- GKE receipt SHA-256: %s\\n'",
        "'- GKE receipt sidecar SHA-256: %s\\n'",
        "'- Release path: landed main -> immutable candidate -> digest-pinned GKE acceptance -> protected annotated tag -> no-rebuild promotion.\\n'",
        r#"printf '%s\n' "$COMPATIBILITY_NOTE""#,
    ] {
        assert_eq!(count(gate), 1, "{gate}");
    }
}
