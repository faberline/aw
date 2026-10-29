"""Offline native-provider controls. No model, network, or product execution."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_qualification as qualification

_spec = importlib.util.spec_from_file_location(
    "native_merge_under_test", Path(__file__).parents[1] / "scripts/native_merge.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
HEAD, BASE, SOURCE, EVALUATOR = (c * 40 for c in "abcd")


class Provider:
    evaluate_policy = staticmethod(m.review.ci.evaluate_policy)
    validate_config = staticmethod(m.review.ci.validate_config)
    validate_adoption = staticmethod(m.review.ci.validate_adoption)
    strict_json_loads = staticmethod(m.review.ci.strict_json_loads)
    process_head = staticmethod(lambda *args, **kwargs: None)

    def __init__(self):
        cfg = qualification.config()
        cfg["claude_final_review"] = dict(
            version=1, producer=dict(path=".github/workflows/review.yml", id=20,
                definition_sha="e" * 40, job="review"),
            merge_producer=None,
            gates={qualification.WORKFLOW: dict(id=77, definition_sha=qualification.BLOB)},
            protected_paths=["scripts/ci/"], cli_sha256="e" * 64,
            credential_profile="personal-claude-max-oauth", merge_method=None)
        cfg["native_auto_merge"] = dict(version=1, enabled=True,
            status_context="claude/verified-merge", status_app_id=15368,
            merge_method="SQUASH", ruleset_id=9,
            producer=dict(path=".github/workflows/issue-ci.yml", id=30,
                definition_sha="f" * 40, job="merge"))
        self.document = dict(config=cfg, source_commit=SOURCE)
        self.pull = dict(number=1, state="open", draft=False, changed_files=1, title="Synthetic PR", body="Closes #7\n",
            head=dict(sha=HEAD, ref="issue/7", repo=dict(full_name="o/r")),
            base=dict(sha=BASE, ref="main", repo=dict(full_name="o/r")))
        self.native = dict(id="PR_1", state="OPEN", isDraft=False,
            headRefOid=HEAD, baseRefOid=BASE, baseRefName="main",
            autoMergeRequest=None, mergeCommit=None)
        self.meta = dict(default_branch="main", allow_auto_merge=True, allow_squash_merge=True)
        self.rules = [dict(type="required_status_checks", ruleset_id=9, parameters=dict(
            strict_required_status_checks_policy=True,
            required_status_checks=[dict(context="claude/verified-merge", integration_id=15368)]))]
        self.ruleset = dict(enforcement="active", target="branch", bypass_actors=[])
        self.receipt = dict(payload=dict(context=dict(repo="o/r", pr=1, head=HEAD,
            base=BASE, base_ref="main")), sha256="a" * 64)
        self.issue=dict(number=7,state="open",title="Contract",body="Implement the contract.",labels=[])
        self.receipt["payload"]["context"].update(
            pr_description_sha256=m.review.r.digest([self.pull["title"],self.pull["body"]]),
            issue_sha256=m.review.r.digest({k:self.issue[k] for k in ("number","title","body")}))
        self.statuses = []
        self.writes = []
        self.verifications = 0
        self.verify_hook = None
        self.error_on = None
        self.auto_complete = True
        self.snapshot_hook = None

    def load(self):
        return copy.deepcopy(self.document)

    def verify(self, *args):
        self.verifications += 1
        if self.verify_hook:
            self.verify_hook(self.verifications)
        return copy.deepcopy(self.receipt)

    def qualify_config(self, repo, pull, config):
        return qualification.scope(config, pull, qualification.files())

    def snapshot(self, repo, head, config):
        data = qualification.evidence(config)
        if self.snapshot_hook:
            self.snapshot_hook(data)
        return data

    def pages(self, path, key=None):
        if "/statuses" in path: return copy.deepcopy(self.statuses)
        raise AssertionError(path)

    def api(self, path, method="GET", data=None):
        if path == "graphql":
            query = data["query"]
            if "mutation" not in query:
                return dict(data=dict(repository=dict(pullRequest=copy.deepcopy(self.native))))
            if "disablePullRequestAutoMerge" in query:
                self.writes.append(("disable", copy.deepcopy(data["variables"])))
                self.native["autoMergeRequest"] = None
                return dict(data=dict(disablePullRequestAutoMerge=dict(pullRequest=copy.deepcopy(self.native))))
            self.writes.append(("queue", copy.deepcopy(data["variables"])))
            if self.error_on == "queue":
                raise TimeoutError("synthetic ambiguous queue result")
            inputs=data["variables"]["input"]
            self.native["autoMergeRequest"] = dict(mergeMethod="SQUASH", enabledAt="2026-10-29T00:00:00Z",
                enabledBy=dict(login="github-actions[bot]"),commitHeadline=inputs["commitHeadline"],commitBody=inputs["commitBody"])
            return dict(data=dict(enablePullRequestAutoMerge=dict(pullRequest=copy.deepcopy(self.native))))
        if method == "POST":
            assert path == "repos/o/r/statuses/" + HEAD
            self.writes.append((data["state"], copy.deepcopy(data)))
            self.statuses.insert(0, dict(data, id=len(self.statuses)+1, creator=dict(login="github-actions[bot]")))
            if self.error_on == data["state"]:
                raise TimeoutError("synthetic ambiguous status result")
            if data["state"] == "success" and self.auto_complete:
                self.native.update(state="MERGED", mergeCommit=dict(oid="f" * 40), autoMergeRequest=None)
            return dict(state=data["state"])
        if path == "repos/o/r": return copy.deepcopy(self.meta)
        if path == "repos/o/r/pulls/1": return copy.deepcopy(self.pull)
        if path == "repos/o/r/issues/7": return copy.deepcopy(self.issue)
        if path == "repos/o/r/rules/branches/main": return copy.deepcopy(self.rules)
        if path == "repos/o/r/rulesets/9": return copy.deepcopy(self.ruleset)
        raise AssertionError(path)

    def run(self):
        return m.enqueue(self, "o/r", 1, HEAD, self.load, EVALUATOR, 200, verify=self.verify)

    def queue_pages(self, path, key=None):
        if "/pulls?" in path: return [copy.deepcopy(self.pull)]
        if "/workflows/" in path: return []
        return self.pages(path, key)


class ActionsProvider(Provider):
    """Match the exact fields observed with the hosted Actions token."""
    def __init__(self):
        super().__init__()
        self.meta=dict(default_branch='main')
        self.ruleset=dict(enforcement='active',target='branch')
        self.graph_repo=dict(nameWithOwner='o/r',autoMergeAllowed=True,squashMergeAllowed=True,
            defaultBranchRef=dict(name='main',target=dict(oid=BASE)))
        self.graph_ruleset=dict(databaseId=9,enforcement='ACTIVE',target='BRANCH',
            bypassActors=dict(totalCount=0,pageInfo=dict(hasNextPage=False)))

    def api(self,path,method='GET',data=None):
        if path=='graphql' and data['query']==m.PROVIDER_REPOSITORY_POLICY:
            return dict(data=dict(repository=copy.deepcopy(self.graph_repo)))
        if path=='graphql' and data['query']==m.PROVIDER_RULESET_POLICY:
            return dict(data=dict(repository=dict(nameWithOwner='o/r',ruleset=copy.deepcopy(self.graph_ruleset))))
        return super().api(path,method,data)


class ActionsProviderVisibilityTests(unittest.TestCase):
    def check(self,p):
        m.base_rules(p,'o/r',dict(base_ref='main',base=BASE),p.document['config']['native_auto_merge'])

    def test_actions_visible_graphql_proves_the_same_strict_policy(self):
        self.check(ActionsProvider())

    def test_unknown_or_changed_repository_settings_refuse(self):
        for field,value in [('nameWithOwner','other/repo'),('autoMergeAllowed',False),('autoMergeAllowed',None),
                            ('squashMergeAllowed',False),('squashMergeAllowed',None),
                            ('defaultBranchRef',None),('defaultBranchRef',{'name':'main','target':{'oid':'f'*40}})]:
            p=ActionsProvider();p.graph_repo[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(RuntimeError):self.check(p)
        for field,value in [('allow_auto_merge',False),('allow_squash_merge',False),('allow_auto_merge',1)]:
            p=ActionsProvider();p.meta[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(RuntimeError):self.check(p)

    def test_unknown_changed_or_nonzero_bypass_refuses(self):
        for field,value in [('databaseId',10),('databaseId',True),('enforcement','DISABLED'),('target','TAG'),
                            ('bypassActors',None),('bypassActors',{'totalCount':1,'pageInfo':{'hasNextPage':False}}),
                            ('bypassActors',{'totalCount':0,'pageInfo':{'hasNextPage':True}}),
                            ('bypassActors',{'totalCount':False,'pageInfo':{'hasNextPage':False}})]:
            p=ActionsProvider();p.graph_ruleset[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(RuntimeError):self.check(p)
        p=ActionsProvider();p.ruleset['bypass_actors']=[{'actor_id':1}]
        with self.assertRaises(RuntimeError):self.check(p)

    def test_required_status_and_strict_mode_remain_mandatory(self):
        p=ActionsProvider();p.rules[0]['parameters']['strict_required_status_checks_policy']=False
        with self.assertRaises(RuntimeError):self.check(p)
        p=ActionsProvider();p.rules[0]['parameters']['required_status_checks']=[]
        with self.assertRaises(RuntimeError):self.check(p)


class NativeMergeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = Provider()

    def test_qualified_pass_queues_while_pending_then_rechecks_before_success(self):
        result = self.p.run()
        self.assertEqual(result, dict(state="merged", head=HEAD, merge="f" * 40))
        self.assertEqual([w[0] for w in self.p.writes], ["pending", "queue", "success"])
        self.assertEqual(self.p.writes[1][1]["input"]["expectedHeadOid"], HEAD)
        self.assertEqual(self.p.writes[1][1]["input"]["mergeMethod"], "SQUASH")
        self.assertEqual(self.p.writes[1][1]["input"]["commitBody"], "Closes #7")
        self.assertEqual(self.p.writes[1][1]["input"]["commitHeadline"], "Merge PR #1")
        self.assertEqual(self.p.verifications, 2)

    def test_provider_request_is_durable_without_an_immediate_merge_call(self):
        self.p.auto_complete = False
        self.assertEqual(self.p.run()["state"], "queued")
        self.assertEqual(self.p.native["autoMergeRequest"]["mergeMethod"], "SQUASH")
        self.p.writes.clear()
        self.assertEqual(self.p.run()["state"], "queued")
        self.assertEqual([w[0] for w in self.p.writes], ["pending", "success"])

    def test_merged_provider_result_is_observed_without_another_write(self):
        self.p.run()
        self.p.writes.clear()
        self.assertEqual(self.p.run()["state"], "already_merged")
        self.assertEqual(self.p.writes, [])

    def test_failure_only_and_qualification_holds_never_write(self):
        for mutate in (lambda c: c.update(lifecycle_mode="failure-only"),
                       lambda c: c.update(qualification_blockers=["unknown performance"]),
                       lambda c: c.pop("qualification")):
            p = Provider(); mutate(p.document["config"])
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError): p.run()
            self.assertEqual(p.writes, [])

    def test_missing_failed_skipped_and_unmapped_evidence_never_write(self):
        for conclusion in ("failure", "skipped", "cancelled", "neutral", None):
            p = Provider()
            p.snapshot_hook = lambda data, c=conclusion: next(iter(data.values()))[1][0].update(conclusion=c)
            with self.subTest(conclusion=conclusion), self.assertRaises(RuntimeError): p.run()
            self.assertEqual(p.writes, [])
        self.p.pull["changed_files"] = 2
        with self.assertRaises(RuntimeError): self.p.run()
        self.assertEqual(self.p.writes, [])

    def test_provider_policy_or_missing_strict_review_status_never_write(self):
        mutations = [lambda p: p.meta.update(allow_auto_merge=False),
                     lambda p: p.meta.update(default_branch="other"),
                     lambda p: p.rules.clear(),
                     lambda p: p.rules[0]["parameters"].update(strict_required_status_checks_policy=False),
                     lambda p: p.rules[0]["parameters"]["required_status_checks"][0].update(integration_id=1),
                     lambda p: p.ruleset.update(enforcement="evaluate"),
                     lambda p: p.ruleset.pop("bypass_actors"),
                     lambda p: p.ruleset.update(bypass_actors=[dict(actor_id=15368)])]
        for mutate in mutations:
            p = Provider(); mutate(p)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError): p.run()
            self.assertEqual(p.writes, [])

    def test_head_base_draft_and_conflicting_method_never_write(self):
        for key, value in (("headRefOid", "f" * 40), ("baseRefOid", "f" * 40),
                           ("baseRefName", "other"), ("isDraft", True),
                           ("autoMergeRequest", dict(mergeMethod="MERGE"))):
            p = Provider(); p.native[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): p.run()
            self.assertEqual(p.writes, [])

    def test_review_head_policy_or_ci_drift_after_queue_cannot_publish_success(self):
        for mutate in (lambda p: p.native.update(headRefOid="f" * 40),
                       lambda p: p.native.update(baseRefOid="f" * 40),
                       lambda p: p.document.update(source_commit="f" * 40),
                       lambda p: p.receipt.update(sha256="f" * 64),
                       lambda p: p.ruleset.update(enforcement="disabled"),
                       lambda p: p.native.update(autoMergeRequest=dict(mergeMethod="MERGE")),
                       lambda p: p.native.update(autoMergeRequest=None),
                       lambda p: setattr(p, "snapshot_hook", lambda d: d.clear())):
            p = Provider()
            p.verify_hook = lambda n, p=p, mutate=mutate: mutate(p) if n == 2 else None
            self.assertEqual(p.run()["state"], "held_after_pending")
            self.assertNotIn("success", [w[0] for w in p.writes])
            self.assertEqual([w[0] for w in p.writes].count("queue"), 1)

    def test_unknown_queue_or_success_response_is_not_retried(self):
        for point in ("queue", "success"):
            p = Provider(); p.error_on = point
            self.assertEqual(p.run()["state"], "held_after_pending")
            self.assertEqual([w[0] for w in p.writes].count("queue"), 1)
            self.assertLessEqual([w[0] for w in p.writes].count("success"), 1)

    def test_merge_job_cannot_have_model_credentials(self):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "synthetic-not-a-credential"
        with self.assertRaises(RuntimeError): self.p.run()
        self.assertEqual(self.p.writes, [])

    def test_refused_first_pr_does_not_stop_the_complete_queue(self):
        pulls = [dict(self.p.pull, number=3), dict(self.p.pull, number=1),
                 dict(self.p.pull, number=2)]
        self.p.pages = lambda *args: pulls if "/pulls?" in args[0] else []
        index = lambda *args: {n: dict(id=200+n, status="completed", conclusion="success")
                              for n in (1, 2, 3)}
        visited = []

        def enqueue(*args, **kwargs):
            number = args[2]; visited.append(number)
            if number == 1: raise m.review.Refused("required_gates_not_success")
            return dict(state="queued", head=HEAD)

        with patch.object(m, "enqueue", side_effect=enqueue):
            rows = m.reconcile(self.p, "o/r", self.p.load, EVALUATOR, index, self.p.verify)
        self.assertEqual(visited, [1, 2, 3])
        self.assertEqual([r["state"] for r in rows], ["held", "queued", "queued"])
        self.assertEqual(rows[0]["reason"], "required_gates_not_success")

    def main_call(self, rows, event=None):
        event = event or dict(action="completed", repository=dict(full_name="o/r"),
            workflow_run=dict(id=200, workflow_id=20,
                path=".github/workflows/review.yml", event="workflow_run"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"; path.write_text(json.dumps(event))
            os.environ.update(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="workflow_run",
                GITHUB_REPOSITORY="o/r", AW_ACTION_REF=EVALUATOR,
                GITHUB_RUN_ID="300", GITHUB_EVENT_PATH=str(path))
            index = SimpleNamespace(attempts=object(), verify=object(), result=object())
            index.Session=lambda *args:index
            with patch.object(m.review, "ci", self.p), \
                 patch.object(m.review.action, "trusted_policy", return_value=self.p.load()), \
                 patch.object(m, "writer_identity") as producer, \
                 patch.object(m.review, "sibling", return_value=index), \
                 patch.object(m, "writer_contract"), \
                 patch.object(m, "reconcile", return_value=rows) as reconcile, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = m.main()
                self.assertEqual(producer.call_args.args[3], 300)
                self.assertIs(reconcile.call_args.args[-1], self.p.process_head)
                self.assertEqual(reconcile.call_count, 1)
                return result

    def test_entrypoint_checks_merge_producer_and_surfaces_unknown_writes(self):
        self.assertEqual(self.main_call([dict(state="queued")]), 0)
        self.assertEqual(self.main_call([dict(state="held_after_pending")]), 2)

    def test_entrypoint_rejects_a_forged_wake_before_reconciliation(self):
        bad = dict(action="completed", repository=dict(full_name="o/r"),
            workflow_run=dict(id=200, workflow_id=999,
                path=".github/workflows/review.yml", event="workflow_run"))
        with self.assertRaisesRegex(RuntimeError, "merge_wake_identity"):
            self.main_call([], event=bad)
        self.assertEqual(self.p.writes, [])


if __name__ == "__main__":
    unittest.main()

class WriterContractTests(unittest.TestCase):
    def setUp(self):
        self.p=Provider()
        root=Path(__file__).parents[1]
        self.native=(root/'templates/claude-native-merge.yml.example').read_text().replace('AW_REVIEWED_40_HEX',EVALUATOR).replace('REVIEWED_EXISTING_TEST_WORKFLOW_NAME','tests')
        self.p.document['config']['native_auto_merge']['producer']['job']='native-auto-merge'
        self.review='name: Claude final review\non:\n  workflow_run:\n    workflows: [tests]\n    types: [completed]\n'

    def validate(self):
        with patch.object(m.review,'blob',side_effect=[self.native,self.review,"name: tests\n"]):
            m.writer_contract(self.p,'o/r',self.p.load(),EVALUATOR)

    def test_literal_single_writer_with_shared_mutex_is_valid(self):self.validate()

    def test_mutex_credential_step_mutable_action_or_extra_job_is_refused(self):
        changes=(lambda s:s.replace('group: issue-ci-handoff','group: different'),
                 lambda s:s.replace('cancel-in-progress: false','cancel-in-progress: true'),
                 lambda s:s.replace('issues: write','issues: read'),
                 lambda s:s.replace('branches: [main]','branches: [codex/dependency]'),
                 lambda s:s.replace('@'+EVALUATOR,'@main'),
                 lambda s:s+'\n  extra:\n    runs-on: ubuntu-latest\n    steps: []\n',
                 lambda s:s.replace('    steps:','    env:\n      SECRET: value\n    steps:'),
                 lambda s:s.replace('workflows: [tests, Claude final review]','workflows: [Untrusted]'))
        original=self.native
        for change in changes:
            self.native=change(original)
            with self.subTest(source=self.native),self.assertRaises(RuntimeError):self.validate()


class ApprovalWithdrawalTests(unittest.TestCase):
    def test_closed_blocked_changed_issue_or_extra_closing_reference_never_queues(self):
        for mutate in (lambda p:p.issue.update(state='closed'),
                       lambda p:p.issue['labels'].append(dict(name='blocked')),
                       lambda p:p.issue.update(body='Changed requirement'),
                       lambda p:p.pull.update(body='Closes #7\nAlso fixes #9\n')):
            p=Provider();mutate(p)
            with self.assertRaises(RuntimeError):p.run()
            self.assertEqual(p.writes,[])

    def test_prior_pass_is_withdrawn_before_stale_receipt_policy_or_missing_review_hold(self):
        for failure in ('receipt', 'policy', 'missing', 'active'):
            p=Provider();p.auto_complete=False;p.run();p.writes.clear()
            original_pages=p.pages
            p.pages=lambda path,key=None: [copy.deepcopy(p.pull)] if '/pulls?' in path else [] if '/workflows/' in path else original_pages(path,key)
            if failure=='policy':p.document['config']['qualification_blockers']=['Coverage unresolved']
            if failure=='receipt':p.verify_hook=lambda n: (_ for _ in ()).throw(m.review.Refused('review_stale'))
            attempts={} if failure=='missing' else {1:dict(id=200,status='in_progress' if failure=='active' else 'completed',conclusion=None if failure=='active' else 'success')}
            rows=m.reconcile(p,'o/r',p.load,EVALUATOR,lambda *a:attempts,p.verify)
            self.assertEqual([w[0] for w in p.writes],['pending','disable'])
            self.assertIsNone(p.native['autoMergeRequest'])
            self.assertEqual(p.statuses[0]['state'],'pending')
            self.assertEqual(rows[0]['state'],'held')

    def test_other_users_unrelated_native_request_is_not_cancelled(self):
        p=Provider();p.native['autoMergeRequest']=dict(mergeMethod='SQUASH',enabledBy=dict(login='human'))
        m.withdraw_approval(p,'o/r',1,HEAD)
        self.assertEqual(p.writes,[])

    def test_unknown_disable_result_does_not_retry_or_restore_success(self):
        p=Provider();p.auto_complete=False;p.run();p.writes.clear()
        orig=p.api
        def api(path,method='GET',data=None):
            if path=='graphql' and 'disablePullRequestAutoMerge' in data['query']:
                orig(path,method,data)
                raise TimeoutError('Response lost after disabling')
            return orig(path,method,data)
        p.api=api
        with self.assertRaises(TimeoutError):m.withdraw_approval(p,'o/r',1,HEAD)
        self.assertEqual([w[0] for w in p.writes],['pending','disable'])
        self.assertIsNone(p.native['autoMergeRequest'])
