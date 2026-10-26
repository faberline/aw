"""Offline byte-boundary tests for action-reference-only candidate changes."""
import copy
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
BASELINE = "112a940c05b48cd2b93afe9007aff92c47c13ca1"
EXPECTED = {"actions/checkout", "actions/upload-artifact", "google-github-actions/auth", "google-github-actions/setup-gcloud"}


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def check(source, evidence):
    assert evidence["status"] == "offline_unpublished_candidate_not_qualified"
    assert evidence["baseline_workflow_blob"] == BASELINE
    assert blob(source.encode()) == evidence["candidate_workflow_blob"]
    assert {item["repo"] for item in evidence["references"]} == EXPECTED
    normalized = source
    for item, replacement in zip(evidence["references"], evidence["replacements"]):
        assert item["source_url"] == "https://api.github.com/repos/" + item["repo"] + "/git/ref/" + item["observed_ref"]
        assert item["ref_object"]["type"] == "commit" and item["ref_object"]["sha"] == item["commit"]
        assert re.fullmatch("[0-9a-f]{40}", item["commit"])
        before = item["repo"] + "@" + item["observed_ref"].split("/", 1)[1]
        after = item["repo"] + "@" + item["commit"]
        assert replacement == dict(before=before, after=after)
        normalized = re.sub(r"(?m)(^\s*-?\s*uses:\s*)" + re.escape(after) + r"(?=\s|$)", lambda m: m[1] + before, normalized)
    assert blob(normalized.encode()) == BASELINE  # All commands/jobs/permissions/input bytes unchanged.
    uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", source)
    assert uses and all(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}", item) for item in uses)


class DependencyPinTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / ".github/workflows/gke-test.yml").read_text()
        self.evidence = json.loads((ROOT / ".github/ci-dependency-pins-candidate.json").read_text())

    def test_actual_candidate_freezes_only_refs_and_preserves_workflow_bytes(self):
        check(self.source, self.evidence)

    def test_command_change_fails_even_with_updated_candidate_digest(self):
        changed = self.source + "\n# unrelated content\n"
        evidence = dict(self.evidence, candidate_workflow_blob=blob(changed.encode()))
        with self.assertRaises(AssertionError): check(changed, evidence)
        check(self.source, self.evidence)

    def test_wrong_owner_or_mutable_ref_is_not_verified_source(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["references"][0]["source_url"] = "https://api.github.com/repos/foreign/checkout/git/ref/tags/v4"
        with self.assertRaises(AssertionError): check(self.source, evidence)
        replacement = self.evidence["replacements"][0]
        changed = self.source.replace(replacement["after"], replacement["before"])
        evidence = dict(self.evidence, candidate_workflow_blob=blob(changed.encode()))
        with self.assertRaises(AssertionError): check(changed, evidence)


if __name__ == "__main__":
    unittest.main()
