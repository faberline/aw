//! The clean-reader task: a prompt that asks a fresh reader to restate a
//! project from its adopted documents only. It is built after the
//! deterministic check passes. The JSON shape is the one of the defer script.

use std::collections::BTreeMap;

use super::project::ProjectReport;

/// A JSON value that keeps the order of its keys.
enum J {
    S(&'static str),
    I(i64),
    A(Vec<J>),
    O(Vec<(String, J)>),
}

fn s(text: &'static str) -> J {
    J::S(text)
}

fn list(text: &'static str) -> J {
    J::A(vec![s(text)])
}

fn obj(items: Vec<(&str, J)>) -> J {
    J::O(items.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

/// Python `json.dumps(value, indent=2)`.
fn dumps(value: &J, depth: usize) -> String {
    let pad = "  ".repeat(depth + 1);
    let close = "  ".repeat(depth);
    match value {
        J::S(text) => quote(text),
        J::I(n) => n.to_string(),
        J::A(items) if items.is_empty() => "[]".into(),
        J::A(items) => {
            let body: Vec<String> = items
                .iter()
                .map(|v| format!("{pad}{}", dumps(v, depth + 1)))
                .collect();
            format!("[\n{}\n{close}]", body.join(",\n"))
        }
        J::O(items) if items.is_empty() => "{}".into(),
        J::O(items) => {
            let body: Vec<String> = items
                .iter()
                .map(|(k, v)| format!("{pad}{}: {}", quote(k), dumps(v, depth + 1)))
                .collect();
            format!("{{\n{}\n{close}}}", body.join(",\n"))
        }
    }
}

fn quote(text: &str) -> String {
    serde_json::to_string(text).expect("a string is JSON")
}

fn contract_map() -> J {
    J::A(vec![obj(vec![
        ("fact", s("<fact class>")),
        ("canonical_source", s("<source>")),
        ("discovery", s("<command or file>")),
    ])])
}

/// The `interfaces` entry of one guide kind, if the kind has one.
fn interface(kind: &str) -> Option<(&'static str, J)> {
    Some(match kind {
        "protocol" => (
            "protocol",
            obj(vec![
                ("contract_map", contract_map()),
                ("operation_families", list("<operation family>")),
                ("current_boundaries", list("<current protocol boundary>")),
            ]),
        ),
        "clients" => (
            "clients",
            obj(vec![
                (
                    "artifact_model",
                    s("<what is generated, committed, or published>"),
                ),
                (
                    "languages",
                    J::A(vec![obj(vec![
                        ("language", s("<language>")),
                        ("generated_form", s("<generated form>")),
                        ("transport", s("<transport>")),
                        ("auth_input", s("<auth input>")),
                        ("current_limits", s("<limits>")),
                    ])]),
                ),
                (
                    "current_boundaries",
                    list("<current generated-client boundary>"),
                ),
            ]),
        ),
        "indexing" => (
            "indexing",
            obj(vec![
                ("contract_map", contract_map()),
                (
                    "data_ownership",
                    list("<source and derived-index boundary>"),
                ),
                (
                    "current_contract",
                    list("<current schema, write, durability, or rebuild fact>"),
                ),
                (
                    "target_contract",
                    list("<future schema, write, durability, or rebuild fact>"),
                ),
                ("current_boundaries", list("<current indexing boundary>")),
            ]),
        ),
        "querying" => (
            "querying",
            obj(vec![
                ("contract_map", contract_map()),
                (
                    "selection_and_hydration",
                    list("<selection and hydration responsibility>"),
                ),
                ("current_contract", list("<current query or result fact>")),
                (
                    "target_contract",
                    list("<future query, facet, metric, or limit fact>"),
                ),
                ("current_boundaries", list("<current querying boundary>")),
            ]),
        ),
        "gke" => (
            "gke",
            obj(vec![
                ("contract_map", contract_map()),
                (
                    "support_tiers",
                    list("<current or target environment and support tier>"),
                ),
                (
                    "runtime_topologies",
                    list("<pod, shard, replica, voter, and HA meaning>"),
                ),
                (
                    "kubernetes_boundary",
                    list("<portable contract and GKE profile boundary>"),
                ),
                (
                    "current_boundaries",
                    list("<current GKE or Kubernetes boundary>"),
                ),
            ]),
        ),
        "client-integration" => (
            "client_integration",
            obj(vec![
                ("contract_map", contract_map()),
                (
                    "responsibility_boundary",
                    list("<server, generated-client, template, or caller owner>"),
                ),
                (
                    "connection_profiles",
                    list("<Standalone or Managed connection behavior>"),
                ),
                (
                    "workload_template",
                    list("<current or target Kubernetes workload behavior>"),
                ),
                (
                    "source_integration",
                    list("<source database and hydration behavior>"),
                ),
                (
                    "current_boundaries",
                    list("<current client-integration boundary>"),
                ),
            ]),
        ),
        _ => return None,
    })
}

fn migration() -> J {
    obj(vec![
        ("path", s("<migration guide path>")),
        (
            "compatibility_rows",
            J::A(vec![obj(vec![
                ("surface", s("<surface>")),
                ("from", s("<0.4.x behavior>")),
                ("to", s("<0.5.0 behavior>")),
                ("required_action", s("<caller action>")),
            ])]),
        ),
        ("activation", list("<managed activation rule>")),
        ("tools", list("<migration tool>")),
    ])
}

fn name_id(name: &'static str) -> J {
    J::A(vec![obj(vec![("name", s(name)), ("id", s("<id>"))])])
}

/// The JSON template of the answer.
fn template(order: &[(String, String)], interfaces: Vec<(String, J)>) -> String {
    let mut output: Vec<(String, J)> = vec![
        (
            "sha256".into(),
            J::O(
                order
                    .iter()
                    .map(|(n, _)| (n.clone(), s("<sha256>")))
                    .collect(),
            ),
        ),
        ("status".into(), s("reviewed | stale_input")),
        (
            "stated".into(),
            obj(vec![
                ("purpose", s("<what the product is>")),
                ("not_for", list("<explicit product boundary>")),
                ("primary_workflow", list("<ordered steps>")),
                ("functional_surfaces", list("<main user-facing functions>")),
                (
                    "capabilities",
                    J::A(vec![obj(vec![
                        ("name", s("<heading>")),
                        ("id", s("<id>")),
                        ("sources", list("<source>")),
                    ])]),
                ),
            ]),
        ),
        (
            "current_support".into(),
            J::A(vec![obj(vec![
                ("name", s("<surface>")),
                ("id", s("<id>")),
                ("state", s("<state>")),
                ("scope", s("<supported scope>")),
                ("limits", s("<limits>")),
            ])]),
        ),
        (
            "future".into(),
            obj(vec![
                ("near_term", name_id("<outcome>")),
                ("later", name_id("<outcome>")),
                ("non_goals", name_id("<non-goal>")),
            ]),
        ),
        ("inferences".into(), list("<clearly labelled inference>")),
        (
            "cross_document_contradictions".into(),
            list("<statement that conflicts across documents>"),
        ),
        (
            "missing_details".into(),
            list("<detail delegated to another maintained source or still hard to find>"),
        ),
        ("comprehension_score".into(), J::I(0)),
    ];
    if !interfaces.is_empty() {
        output.push(("interfaces".into(), J::O(interfaces)));
    }
    dumps(&J::O(output), 0)
}

/// The prompt for a project that passed the check. `root` is the absolute
/// path of the checkout and `dir` the project folder in it (empty for the
/// root). `None` when a core document is missing.
pub fn clean_reader(report: &ProjectReport, root: &str, dir: &str) -> Option<String> {
    let (readme, status, roadmap) = (
        report.readme.as_ref()?,
        report.status.as_ref()?,
        report.roadmap.as_ref()?,
    );
    let (base, prefix) = if dir.is_empty() {
        (root.to_string(), String::new())
    } else {
        (format!("{root}/{dir}"), format!("{dir}/"))
    };
    let mut order: Vec<(String, String)> = vec![
        ("README.md".into(), readme.sha256.clone()),
        ("STATUS.md".into(), status.sha256.clone()),
        ("ROADMAP.md".into(), roadmap.sha256.clone()),
    ];
    let mut paths: Vec<String> = order.iter().map(|(n, _)| format!("{base}/{n}")).collect();
    let mut kinds: Vec<&str> = Vec::new();
    for doc in &report.supporting_docs {
        let relative = doc.path.strip_prefix(&prefix).unwrap_or(&doc.path);
        order.push((relative.to_string(), doc.sha256.clone()));
        paths.push(format!("{base}/{relative}"));
        kinds.push(&doc.kind);
    }
    let expected: BTreeMap<&str, &str> = order
        .iter()
        .map(|(n, h)| (n.as_str(), h.as_str()))
        .collect();

    let mut interfaces: Vec<(String, J)> = Vec::new();
    for kind in [
        "protocol",
        "clients",
        "indexing",
        "querying",
        "gke",
        "client-integration",
    ] {
        if kinds.contains(&kind)
            && let Some((key, value)) = interface(kind)
        {
            interfaces.push((key.to_string(), value));
        }
    }
    let migrations: Vec<J> = kinds
        .iter()
        .filter(|k| **k == "migration")
        .map(|_| migration())
        .collect();
    if !migrations.is_empty() {
        interfaces.push(("migrations".into(), J::A(migrations)));
    }
    let instruction = if interfaces.is_empty() {
        ""
    } else {
        " Also list every contract-map row from each adopted guide, every generated-client \
language row, and every migration compatibility row from the adopted supporting guides. Keep \
current behavior separate from target behavior."
    };
    let expected_json = format!(
        "{{{}}}",
        expected
            .iter()
            .map(|(k, v)| format!("{}: {}", quote(k), quote(v)))
            .collect::<Vec<_>>()
            .join(", ")
    );
    let list: Vec<String> = paths.iter().map(|p| format!("- {p}")).collect();
    Some(format!(
        "Read only these {count} files:\n{list}\n\n\
Do not inspect any other file, repository state, prior draft, conversation, or\n\
validator output. Do not edit anything. Compute each file SHA-256 yourself.\n\
The expected SHA-256 map is {expected_json}. Stop and\n\
report stale_input if any value differs.\n\n\
Act as a first-time developer who must understand what the product does, what\n\
is supported now, and what is future work. Return JSON only:\n\n\
{template}\n\n\
List every capability, support surface, roadmap outcome, and non-goal.{instruction} Keep\n\
document facts separate from inferences. The score is diagnostic. Do not turn\n\
it into the verdict. Use an integer from 0 to 100. Use 100 when a first-time\n\
reader can recover every requested fact, state, boundary, and document\n\
relationship without a contradiction. Lower the score for an omitted or\n\
unclear requested fact. Do not lower it only because a detail is clearly\n\
delegated to a named maintained source.\n",
        count = paths.len(),
        list = list.join("\n"),
        template = template(&order, interfaces),
    ))
}
