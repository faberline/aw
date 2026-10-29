"""Per-wake metadata index; only latest candidates can request source/log data."""
import copy
import importlib.util
from pathlib import Path
import re

_spec=importlib.util.spec_from_file_location('aw_native_index_review',Path(__file__).with_name('claude_review.py'))
review=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)
TITLE=re.compile(r'AW Claude review PR ([1-9][0-9]*) head ([0-9a-f]{40})')


def attempts(api,repo,document,runs,targets=None,default_branch=None):
    """Index metadata only. This is candidate selection, never approval."""
    spec=review.policy(document)['producer']
    default=default_branch or api.api(f'repos/{repo}')['default_branch']
    latest,run_ids,run_numbers,invalid={},{},{},set()
    for run in runs:
        if (run.get('workflow_id')!=spec['id'] or run.get('path')!=spec['path']
                or run.get('event')!='workflow_run' or run.get('head_branch')!=default
                or run.get('head_repository',{}).get('full_name')!=repo):
            continue
        title=run.get('display_title')
        target=TITLE.fullmatch(title) if isinstance(title,str) else None
        number,head=(int(target[1]),target[2]) if target else (None,None)
        if not all(review.integer(run.get(k)) for k in ('id','run_number','run_attempt')):
            if number is not None and (targets is None or targets.get(number)==head):invalid.add(number)
            continue
        # A workflow run ID and run number each identify one run. A repeated
        # or conflicting page is not an ordering tie: neither target may use
        # an arbitrarily selected result as its latest review.
        if run['id'] in run_ids:
            invalid.update(n for n in (number,run_ids[run['id']]) if n is not None)
        if run['run_number'] in run_numbers:
            invalid.update(n for n in (number,run_numbers[run['run_number']]) if n is not None)
        run_ids[run['id']]=number
        run_numbers[run['run_number']]=number
        if number is None or (targets is not None and targets.get(number)!=head):continue
        candidate=dict(run)
        prior=latest.get(number)
        if prior is None or (run['run_number'],run['run_attempt']) >= (prior['run_number'],prior['run_attempt']):
            latest[number]=candidate
    for number in invalid:latest[number]={'_native_index_error':'review_attempt_identity_unknown'}
    return latest


class ImmutableApi:
    """Cache only content/tree reads addressed by immutable Git object IDs."""
    def __init__(self,api):self.raw,self.cache=api,{}
    def __getattr__(self,name):return getattr(self.raw,name)
    def api(self,path,method='GET',data=None):
        immutable=(method=='GET' and data is None and
            (re.search(r'/contents/.+\?ref=[0-9a-f]{40}$',path)
             or re.search(r'/git/trees/[0-9a-f]{40}(?:\?recursive=1)?$',path)))
        if not immutable:return self.raw.api(path,method,data)
        if path not in self.cache:self.cache[path]=self.raw.api(path,method,data)
        return copy.deepcopy(self.cache[path])


class Session:
    """One complete index per wake, with bounded delta and candidate refreshes.

    Historical runs never cause per-run source/job/log requests. Freshness reads
    cover runs created since our last high-water run and the selected run's
    current attempt. Only immutable source and completed job logs are cached.
    """
    def __init__(self,api,repo,load_document,evaluator,log_reader=review.r.job_log):
        self.api=ImmutableApi(api);self.repo=repo;self.load=load_document;self.evaluator=evaluator
        self.document=load_document();self.spec=review.policy(self.document)['producer']
        self.default=api.api(f'repos/{repo}')['default_branch']
        self.reader=log_reader;self.logs={};self.runs=None;self.targets=None;self.high=None;self.current={}

    def rebuild(self):
        self.current=attempts(self.api,self.repo,self.document,self.runs,self.targets,self.default)
        valid=[r for r in self.runs if review.integer(r.get('run_number')) and review.integer(r.get('id'))]
        self.high=max(valid,key=lambda r:r['run_number'])['id'] if valid else None

    def attempts(self,api,repo,document,runs,targets=None):
        review.need(repo==self.repo and document==self.document,'policy_changed')
        self.runs=list(runs);self.targets=targets;self.rebuild()
        return self.current

    def ensure(self,number,head):
        if self.runs is None:
            rows=self.api.pages(f'repos/{self.repo}/actions/workflows/{self.spec["id"]}/runs','workflow_runs')
            self.attempts(self.api,self.repo,self.document,rows,{number:head})
        review.need(self.load()==self.document,'policy_changed')

    def update(self,run):
        found=[i for i,r in enumerate(self.runs) if r.get('id')==run.get('id')]
        if not found:self.runs.append(run)
        else:
            for index in found:self.runs[index]=run

    def history(self,number):
        boundary=self.high;delta=[];seen=set()
        for page in range(1,11):
            value=self.api.api(f'repos/{self.repo}/actions/workflows/{self.spec["id"]}/runs?per_page=100&page={page}')
            batch=value.get('workflow_runs') if isinstance(value,dict) else None
            review.need(isinstance(batch,list) and len(batch)<=100,'review_delta_unknown')
            for run in batch:
                review.need(review.integer(run.get('id')) and run['id'] not in seen,'review_delta_identity')
                seen.add(run['id']);delta.append(run)
            if any(r['id']==boundary for r in batch) or len(batch)<100:break
        else:raise review.Refused('review_delta_limit')
        for run in delta:self.update(run)
        self.rebuild()
        selected=self.current.get(number)
        if selected and review.integer(selected.get('id')):
            self.update(self.api.api(f'repos/{self.repo}/actions/runs/{selected["id"]}'))
            self.rebuild()  # Covers reruns of an old run outside the first page.
        return self.runs

    def result(self,api,repo,number,head,load_document,evaluator,run_id):
        return self._result(api,repo,number,head,load_document,evaluator,run_id,merged=False)

    def _result(self,api,repo,number,head,load_document,evaluator,run_id,merged):
        self.ensure(number,head)
        review.need(repo==self.repo and evaluator==self.evaluator,'review_session_identity')
        def select(values):
            row=self.current.get(number)
            review.need(row is not None and not row.get('_native_index_error'),'review_target_record_unknown')
            return copy.deepcopy(row)
        def logs(repo,job):
            if job not in self.logs:self.logs[job]=self.reader(repo,job)
            raw=self.logs[job]
            records=review.r.records(raw,review.TARGET_MARKER)
            review.need(len(records)==1 and review.exact(records[0],'version repo pr head')
                and type(records[0]['version']) is int and records[0]['version']==1
                and review.integer(records[0]['pr']) and records[0]==dict(version=1,repo=repo,pr=number,head=head),
                'review_target_record_unknown')
            return raw
        if merged:
            return review.verify_merged_receipt(self.api,repo,number,head,load_document,evaluator,run_id,
                log_reader=logs,latest_selector=select,history_loader=lambda:self.history(number))
        return review.verify_result_receipt(self.api,repo,number,head,load_document,evaluator,run_id,
            log_reader=logs,latest_selector=select,allow_rejected=True,history_loader=lambda:self.history(number))

    def verify(self,*args):
        receipt=self.result(*args)
        review.need(receipt['payload']['result']['decision']=='pass','review_not_pass')
        return receipt

    def verify_merged(self,*args):
        return self._result(*args,merged=True)


def result(api,repo,number,head,load_document,evaluator,run_id,log_reader=review.r.job_log):
    return Session(api,repo,load_document,evaluator,log_reader).result(
        api,repo,number,head,load_document,evaluator,run_id)


def verify(api,repo,number,head,load_document,evaluator,run_id,log_reader=review.r.job_log):
    return Session(api,repo,load_document,evaluator,log_reader).verify(
        api,repo,number,head,load_document,evaluator,run_id)
