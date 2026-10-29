"""Candidate policy inspection is read-only and never grants qualification."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import test_issue_contract_v2 as v2

path=Path(__file__).parents[1]/'scripts'/'proposal_contract.py'
spec=importlib.util.spec_from_file_location('proposal_contract_test',path)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def blob(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def fixture():
    pull=v2.pull();event={'repository':{'full_name':'o/r'},'number':8,'pull_request':copy.deepcopy(pull)}
    raw=json.dumps(v2.policy()).encode()
    data={'type':'file','encoding':'base64','sha':blob(raw),'size':len(raw),'content':base64.b64encode(raw).decode()}
    return event,pull,v2.issue(),data


def run(event,pull,issue,data):
    calls=[]
    def api(path):
        calls.append(path)
        if path=='repos/o/r/pulls/8':return copy.deepcopy(pull)
        if path=='repos/o/r/issues/7':return copy.deepcopy(issue)
        if path==f'repos/o/r/contents/{p.POLICY}?ref={v2.base.HEAD}':return copy.deepcopy(data)
        raise AssertionError(path)
    return p.inspect('o/r',event,api),calls


class ProposalTests(unittest.TestCase):
    def test_exact_head_data_only_does_not_authorize_policy(self):
        result,calls=run(*fixture())
        self.assertEqual(result['qualification'],'not_evaluated')
        self.assertEqual(result['policy_approval'],'not_evaluated')
        self.assertEqual(result['head'],v2.base.HEAD)
        self.assertEqual(len(calls),5)

    def test_moved_pr_and_changed_issue_fail_closed(self):
        for change in ('head','base','fork','issue','body','size','encoding','blob'):
            event,pull,issue,data=fixture()
            if change=='head':pull['head']['sha']='c'*40
            if change=='base':pull['base']['sha']='c'*40
            if change=='fork':pull['head']['repo']['full_name']='x/r'
            if change=='issue':issue['body']='changed'
            if change=='body':pull['body']='Closes #7.'
            if change=='size':data['size']+=1
            if change=='encoding':data['content']='?'
            if change=='blob':data['sha']='e'*40
            with self.subTest(change=change),self.assertRaises(RuntimeError):run(event,pull,issue,data)

    def test_recheck_detects_midflight_pr_or_issue_changes(self):
        for target in ('pr','issue'):
            event,pull,issue,data=fixture();counts={'pr':0,'issue':0}
            def api(path):
                if '/contents/' in path:return data
                if '/pulls/' in path:
                    counts['pr']+=1;item=copy.deepcopy(pull)
                    if target=='pr' and counts['pr']==2:item['title']='changed'
                    return item
                counts['issue']+=1;item=copy.deepcopy(issue)
                if target=='issue' and counts['issue']==2:item['body']='changed'
                return item
            with self.subTest(target=target),self.assertRaisesRegex(RuntimeError,'changed during inspection'):
                p.inspect('o/r',event,api)

    def test_unresolved_candidate_is_never_reported_success(self):
        event,pull,issue,data=fixture();config=v2.policy();config['qualification']['contracts']['7']['unresolved']=['e2e evidence missing']
        raw=json.dumps(config).encode();data.update(size=len(raw),sha=blob(raw),content=base64.b64encode(raw).decode())
        with self.assertRaisesRegex(RuntimeError,'unresolved'):run(event,pull,issue,data)

class EventBoundaryTests(unittest.TestCase):
    def test_mismatched_event_repository_number_head_or_base_refuse(self):
        for change in ('repository','number','head','base'):
            event,pull,issue,data=fixture()
            if change=='repository':event['repository']['full_name']='x/r'
            if change=='number':event['number']=9
            if change=='head':event['pull_request']['head']['sha']='c'*40
            if change=='base':event['pull_request']['base']['sha']='c'*40
            with self.subTest(change=change),self.assertRaises(RuntimeError):run(event,pull,issue,data)

    def test_wrong_event_name_is_refused_before_read_or_network(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'push'},clear=True),patch.object(p.ci,'api') as api:
            with self.assertRaisesRegex(RuntimeError,'pull_request event required'):p.main()
            api.assert_not_called()
