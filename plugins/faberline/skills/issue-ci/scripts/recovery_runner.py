"""Draft native recovery coordinator; no workflow or schedule calls this yet.

This requires a fully deployed shared guard that every active worker and
writer obeys. The current repository does not yet establish that condition.
It must not be enabled until the adoption and crash-recovery gaps are closed.
"""
import importlib.util
from pathlib import Path
import re


def sibling(name):
    spec=importlib.util.spec_from_file_location('aw_recovery_runner_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


scan=sibling('recovery_scan')
close=sibling('guarded_close')
admission=sibling('guarded_admission')
worker=close.worker
need=close.need


def reconcile(api,repo,log_reader):
    """Reauthenticate each eligible candidate; one hold never hides another."""
    policy=worker.read_policy(api,repo)
    need(policy is not None,'merge_guard_policy_missing')
    candidates=scan.scan(api,repo,policy,log_reader);results=[]
    for candidate in candidates:
        if candidate['state'] not in ('guard_required','observed_closed','admission_recovery','closed_prepared_recovery'):
            results.append(candidate);continue
        row=dict(issue=candidate['issue'],pr=candidate['pr'],head=candidate['head'],state='held')
        try:
            run,attempt=candidate['run'],candidate['attempt']
            if candidate.get('kind')=='adoption':
                adoption=scan.adoption_module()
                source=adoption.discover_source(api,repo,run,attempt,candidate['issue'],candidate['head'],
                                                candidate['owner'],log_reader)
                if candidate['state']=='closed_prepared_recovery':
                    result=close.release_adoption_closed_prepared(api,repo,source['document'],policy,source['evaluator'],
                        run,attempt,candidate['issue'],candidate['head'],candidate['owner'],log_reader)
                elif candidate['state']=='admission_recovery':
                    result=admission.cancel_blocked(api,repo,policy,source['intent'])
                else:
                    result=close.reconcile_adoption(api,repo,source['document'],policy,source['evaluator'],run,attempt,
                                                    candidate['issue'],candidate['head'],candidate['owner'],log_reader)
                row.update(result);results.append(row);continue
            document,evaluator=scan.historical_document(api,repo,run,attempt)
            if candidate['state']=='closed_prepared_recovery':
                result=close.release_closed_prepared(api,repo,document,policy,evaluator,run,attempt,
                    candidate['pr'],candidate['head'],candidate['owner'],log_reader)
            elif candidate['state']=='admission_recovery':
                intent=scan.guard.identify_native_owner(api,repo,document,evaluator,run,attempt,
                    candidate['issue'],candidate['head'],candidate['owner'],log_reader)
                result=admission.cancel_blocked(api,repo,policy,intent)
            else:
                result=close.reconcile_native(api,repo,document,policy,evaluator,run,attempt,
                                              candidate['pr'],candidate['head'],log_reader)
            row.update(result)
        except Exception as error:
            reason=str(error)
            row['reason']=reason if re.fullmatch(r'[a-z0-9_]+',reason) else 'recovery_evidence_unknown'
        results.append(row)
    return results
