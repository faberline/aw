"""Synthetic planner-wire protocol checks; no CLI, API, secrets or network.

Interoperability cases use an existing optional jsonschema installation. They
skip visibly when unavailable; runtime diagnostic regressions always run. This
does not add a production dependency or install anything.
"""
import contextlib
import copy
import io
import re
import unittest

from test_claude_review import g, Harness

try:
    import jsonschema
except ImportError:
    jsonschema = None


def protocol_regex_cases():
    cases = [(g.PLAN_TEXT_PATTERN, v, expected) for v, expected in
             [("x", True), (" \t\n", False), ("\u0085", False), ("\ufeff", False),
              ("\u200b", True), (" \u0085x", True)]]
    return cases + [(g.PLAN_PATH_PATTERN, v, expected) for v, expected in
                   [("src/a.rs", True), ("資料/模組.rs", True), ("a/...", True),
                    ("a/..", False), ("/a", False), ("a//b", False), ("a\n", False), ("a\\b", False)]]


class PlannerDiagnosticsTests(unittest.TestCase):
    def rejected(self, plan, issue, code):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(g.Refused) as error:
                g.validate_plan(plan, issue)
        self.assertEqual(str(error.exception), code)
        self.assertEqual(output.getvalue(), "")

    def test_requirement_failure_conditions_have_distinct_fixed_codes(self):
        cases = [(lambda p: p.update(extra="synthetic-private-marker"), "schema"),
                 (lambda p: p.update(version=True), "version"),
                 (lambda p: p.update(requirements=[]), "requirement_count"),
                 (lambda p: p.update(adopted_specs=[{}] * 17), "spec_count"),
                 (lambda p: p.update(uncertainties=["x"] * 17), "uncertainty_count"),
                 (lambda p: p.update(uncertainties=[" \t"]), "uncertainty_text"),
                 (lambda p: p["requirements"][0].update(extra="synthetic-private-marker"), "requirement_shape"),
                 (lambda p: p["requirements"][0].update(id="R1"), "requirement_id"),
                 (lambda p: p["requirements"].append(dict(p["requirements"][0], requirement="Different text")), "requirement_id_duplicate"),
                 (lambda p: p["requirements"][0].update(requirement=" \n"), "requirement_text"),
                 (lambda p: p["requirements"][0].update(issue_quote=" \n"), "requirement_quote"),
                 (lambda p: p["requirements"][0].update(issue_quote="synthetic-private-marker"), "requirement_quote_not_literal"),
                 (lambda p: p["requirements"][0].update(supporting_paths=["x"] * 17), "path_count"),
                 (lambda p: p["requirements"][0].update(supporting_paths=["../synthetic-private-marker"]), "path_syntax"),
                 (lambda p: p["requirements"][0].update(supporting_paths=["src/a", "src/a"]), "path_duplicate")]
        for mutate, suffix in cases:
            h = Harness(); plan = copy.deepcopy(h.plan); mutate(plan)
            with self.subTest(code=suffix):
                self.rejected(plan, h.issue, "context_plan_" + suffix)

    def test_spec_relationships_keep_separate_noncontent_diagnostics(self):
        cases = [(lambda p: p["adopted_specs"][0].update(extra=True), "shape"),
                 (lambda p: p["adopted_specs"][0].update(path="../outside"), "path"),
                 (lambda p: p["requirements"][0].update(supporting_paths=[]), "source_not_requested"),
                 (lambda p: p["adopted_specs"].append(dict(p["adopted_specs"][0], issue_quote="Again docs/spec.md")), "duplicate"),
                 (lambda p: p["adopted_specs"][0].update(issue_quote=" \n"), "quote"),
                 (lambda p: p["adopted_specs"][0].update(issue_quote="Review one source file."), "path_not_in_quote"),
                 (lambda p: p["adopted_specs"][0].update(issue_quote="Not in issue: docs/spec.md"), "quote_not_literal")]
        for mutate, suffix in cases:
            h = Harness(); h.issue["body"] += "\nAdopt docs/spec.md\nAgain docs/spec.md"
            plan = copy.deepcopy(h.plan)
            plan["requirements"][0]["supporting_paths"] = ["docs/spec.md"]
            plan["adopted_specs"] = [dict(path="docs/spec.md", issue_quote="Adopt docs/spec.md")]
            mutate(plan)
            with self.subTest(code=suffix):
                self.rejected(plan, h.issue, "context_plan_spec_" + suffix)

    def test_total_requested_source_bound_still_cannot_be_spread_across_requirements(self):
        h = Harness(); plan = copy.deepcopy(h.plan)
        plan["requirements"][0]["supporting_paths"] = ["src/a" + str(i) for i in range(9)]
        plan["requirements"].append(dict(plan["requirements"][0], id="r2", supporting_paths=["src/b" + str(i) for i in range(8)]))
        self.rejected(plan, h.issue, "context_plan_source_count")

    def test_numeric_version_one_matches_json_schema_integer_semantics(self):
        h = Harness()
        for version in (1, 1.0):
            plan = dict(h.plan, version=version)
            self.assertEqual(g.validate_plan(plan, h.issue), plan)
        for version in (True, False, "1", 1.5, 0, None):
            self.rejected(dict(h.plan, version=version), h.issue, "context_plan_version")

    def test_protocol_regex_defined_whitespace_and_path_cases(self):
        for pattern, value, expected in protocol_regex_cases():
            self.assertEqual(re.search(pattern, value) is not None, expected)


@unittest.skipIf(jsonschema is None, "existing optional jsonschema unavailable; interoperability audit not run")
class PlannerSchemaInteroperabilityTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.validators = [version(g.plan_schema()) for version in
                           (jsonschema.Draft7Validator, jsonschema.Draft202012Validator)]
        for version in (jsonschema.Draft7Validator, jsonschema.Draft202012Validator):
            version.check_schema(g.plan_schema())

    def parity(self, plan, expected, issue=None):
        issue = issue or self.h.issue
        for validator in self.validators:
            self.assertEqual(validator.is_valid(plan), expected)
        try:
            g.validate_plan(plan, issue)
            accepted = True
        except g.Refused:
            accepted = False
        self.assertEqual(accepted, expected)

    def test_ids_use_one_small_schema_and_runtime_convention(self):
        for value in list(g.PLAN_REQUIREMENT_IDS) + ["R1", "r0", "r17", "req1", "r1\n", "", 1, None]:
            plan = copy.deepcopy(self.h.plan); plan["requirements"][0]["id"] = value
            with self.subTest(value=value): self.parity(plan, isinstance(value, str) and value in g.PLAN_REQUIREMENT_IDS)

    def test_text_length_and_whitespace_constraints_agree(self):
        for value, expected in [("x", True), ("x" * 2000, True), ("x" * 2001, False),
                                ("", False), (" \t\n", False), ("\u0085", False), ("\ufeff", False), (None, False)]:
            for field in ("requirement", "issue_quote", "uncertainty"):
                plan = copy.deepcopy(self.h.plan); issue = copy.deepcopy(self.h.issue)
                if field == "uncertainty": plan["uncertainties"] = [value]
                else:
                    plan["requirements"][0][field] = value
                    if field == "issue_quote" and isinstance(value, str): issue["body"] += "\n" + value
                with self.subTest(field=field, expected=expected): self.parity(plan, expected, issue)

    def test_relative_path_grammar_and_length_agree(self):
        valid = ["src/a.rs", "資料/模組.rs", ".config/file", "a/...", "x" * 1024]
        invalid = ["", "/a", "a/", "a//b", "./a", "a/../b", "a/.", "..", "a\\b", "a\n", "a\x00b", "x" * 1025]
        for value in valid + invalid:
            plan = copy.deepcopy(self.h.plan); plan["requirements"][0]["supporting_paths"] = [value]
            with self.subTest(value=repr(value)): self.parity(plan, value in valid)

    def test_array_shape_count_and_whole_item_uniqueness_agree(self):
        cases = [dict(self.h.plan, version=1.0), dict(self.h.plan, version=True),
                 dict(self.h.plan, requirements=[]), dict(self.h.plan, requirements=[self.h.plan["requirements"][0]] * 2),
                 dict(self.h.plan, requirements=[self.h.plan["requirements"][0]] * 17),
                 dict(self.h.plan, uncertainties=["x"] * 16), dict(self.h.plan, uncertainties=["x"] * 17),
                 dict(self.h.plan, adopted_specs=[{}] * 17), dict(self.h.plan, extra="not permitted")]
        for plan, expected in zip(cases, [True, False, False, False, False, True, False, False, False]):
            self.parity(plan, expected)
        plan = copy.deepcopy(self.h.plan); plan["requirements"][0]["supporting_paths"] = ["src/a", "src/a"]
        self.parity(plan, False)

    def test_adopted_spec_path_and_quote_lexical_constraints_agree(self):
        path = "docs/spec.md"
        for quote, expected in [("Adopt " + path, True), (path + "x" * (2000 - len(path)), True),
                                (path + "x" * (2001 - len(path)), False), (" \t\n", False)]:
            plan = copy.deepcopy(self.h.plan); issue = copy.deepcopy(self.h.issue)
            plan["requirements"][0]["supporting_paths"] = [path]
            plan["adopted_specs"] = [dict(path=path, issue_quote=quote)]
            issue["body"] += "\n" + quote
            self.parity(plan, expected, issue)
        for selected in ("../spec.md", "/spec.md", "a//b", "a\\b", "x" * 1025):
            plan = copy.deepcopy(self.h.plan)
            plan["requirements"][0]["supporting_paths"] = [selected]
            plan["adopted_specs"] = [dict(path=selected, issue_quote="Adopt " + selected)]
            issue = dict(self.h.issue, body=self.h.issue["body"] + "\nAdopt " + selected)
            self.parity(plan, False, issue)

    def test_all_nested_required_shapes_reject_missing_and_extra_fields(self):
        for key in ("id", "requirement", "issue_quote", "supporting_paths"):
            plan = copy.deepcopy(self.h.plan); del plan["requirements"][0][key]
            self.parity(plan, False)
        plan = copy.deepcopy(self.h.plan); plan["requirements"][0]["extra"] = "untrusted"
        self.parity(plan, False)
        plan = copy.deepcopy(self.h.plan); plan["adopted_specs"] = [dict(path="src/support.py")]
        self.parity(plan, False)

    def test_data_dependent_guards_remain_stricter_than_static_schema(self):
        cases = []
        plan = copy.deepcopy(self.h.plan); plan["requirements"][0]["issue_quote"] = "Paraphrase, not original issue text."
        cases.append((plan, "context_plan_requirement_quote_not_literal"))
        plan = copy.deepcopy(self.h.plan); plan["requirements"].append(dict(plan["requirements"][0], requirement="Different requirement text"))
        cases.append((plan, "context_plan_requirement_id_duplicate"))
        for plan, code in cases:
            self.assertTrue(all(v.is_valid(plan) for v in self.validators))
            with self.assertRaises(g.Refused) as error: g.validate_plan(plan, self.h.issue)
            self.assertEqual(str(error.exception), code)


if __name__ == "__main__":
    unittest.main()
