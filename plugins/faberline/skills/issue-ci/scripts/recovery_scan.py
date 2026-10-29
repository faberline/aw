"""Draft read-only recovery scan; discovery results never authorize writes."""
import importlib.util
from pathlib import Path
import re
from urllib.parse import quote


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_recovery_scan_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


discovery=sibling('guard_discovery')
guard=sibling('merge_guard')
observer=sibling('post_merge_observation')
closer=sibling('guarded_close')
review=guard.review
need=guard.need


def historical_document(api,repo,run,attempt):
    """Read immutable policy for the exact run, not the current PR branch."""
    need(isinstance(repo,str) and guard.provider.ledger.REPO.fullmatch(repo)
         and type(run) is int and run>0 and type(attempt) is int and attempt>0,'recovery_source')
    meta=api.api(f'repos/{repo}');branch=meta.get('default_branch')
    need(meta.get('full_name')==repo and review.text(branch,255),'recovery_source')
    row=api.api(f'repos/{repo}/actions/runs/{run}/attempts/{attempt}')
    source=row.get('head_sha') if isinstance(row,dict) else None
    need(isinstance(row,dict) and row.get('id')==run and row.get('run_attempt')==attempt
         and row.get('status')=='completed' and row.get('head_branch')==branch
         and row.get('head_repository',{}).get('full_name')==repo
         and row.get('event') in ('issues','workflow_run','repository_dispatch')
         and review.sha(source),'recovery_source')
    comparison=api.api(f'repos/{repo}/compare/{source}...{quote(branch,safe="")}')
    need(comparison.get('status') in ('identical','ahead')
         and comparison.get('base_commit',{}).get('sha')==source,'recovery_source')
    raw=review.blob(api,repo,'.github/issue-ci-required-gates.json',source)
    config=review.ci.strict_json_loads(raw)
    need(isinstance(config,dict) and review.sha(config.get('aw_revision')),'recovery_source')
    return dict(config=config,source_commit=source),config['aw_revision']


def scan(api,repo,current_policy,log_reader):
    """Inspect all guards, retaining failures and never mutating an issue."""
    items=discovery.inspect(api,repo,current_policy);results=[]
    for item in items:
        if item['state']!='merge_held':
            results.append(item);continue
        row=dict(issue=item['issue'],state='held')
        try:
            run,attempt=item['run'],item['attempt']
            document,evaluator=historical_document(api,repo,run,attempt)
            intent=guard.identify_native_owner(api,repo,document,evaluator,run,attempt,item['issue'],
                                               item['head'],item['owner'],log_reader)
            pr=intent['payload']['pr']
            evidence=closer.authenticate_checkpoint(api,repo,document,evaluator,run,attempt,pr,item['head'],log_reader)
            need(evidence['payload']['source_attempt']==attempt
                 and evidence['payload']['checkpoint']['issue']==item['issue'], 'recovery_checkpoint_mismatch')
            started=observer.source_started_at(api,evidence)
            observed=observer.recovery_plan(api,evidence['payload']['checkpoint'],started)
            row.update(state=observed['state'],pr=pr,head=item['head'],close_authorized=False)
        except Exception as error:
            reason=str(error)
            if not re.fullmatch(r'[a-z0-9_]+',reason):reason='recovery_evidence_unknown'
            row['reason']=reason
            row['close_authorized']=False
        results.append(row)
    return results
