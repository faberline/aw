"""Draft paginated guard discovery. It never changes refs or issues.

A pass is not an atomic snapshot of GitHub. New or changed refs require a
later wake. The caller must authenticate every reported owner independently.
"""
import importlib.util
from pathlib import Path
import re

_spec=importlib.util.spec_from_file_location('aw_discovery_provider',Path(__file__).with_name('guard_github.py'))
provider=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(provider)
need=provider.need
MAX_REFS=100000
QUERY='''query($owner:String!,$name:String!,$after:String){
  repository(owner:$owner,name:$name){nameWithOwner
    refs(refPrefix:"refs/heads/aw-issue-guard/",first:100,after:$after){
      totalCount nodes{name target{__typename oid}} pageInfo{hasNextPage endCursor}
    }
  }
}'''


def list_refs(api,repo):
    need(isinstance(repo,str) and provider.ledger.REPO.fullmatch(repo),'guard_discovery_repo')
    owner,repo_name=repo.split('/');after=None;seen_cursors=set();seen_refs=set();rows=[];total=None
    while True:
        data=provider.graphql(api,QUERY,dict(owner=owner,name=repo_name,after=after))
        repository=data.get('repository')
        need(isinstance(repository,dict) and repository.get('nameWithOwner')==repo
             and isinstance(repository.get('refs'),dict),'guard_discovery_unknown')
        refs=repository['refs'];nodes=refs.get('nodes');page=refs.get('pageInfo');count=refs.get('totalCount')
        need(type(count) is int and 0<=count<=MAX_REFS and isinstance(nodes,list) and len(nodes)<=100
             and isinstance(page,dict) and type(page.get('hasNextPage')) is bool,'guard_discovery_unknown')
        if total is None:total=count
        need(count==total,'guard_discovery_changed')
        for node in nodes:
            name=node.get('name') if isinstance(node,dict) else None
            match=re.fullmatch(r'aw-issue-guard/issue-([1-9][0-9]{0,9})',name or '')
            target=node.get('target') if isinstance(node,dict) else None
            need(match is not None and name not in seen_refs and isinstance(target,dict)
                 and target.get('__typename')=='Commit' and isinstance(target.get('oid'),str)
                 and provider.ledger.SHA.fullmatch(target['oid']),'guard_discovery_unknown')
            seen_refs.add(name);rows.append(dict(issue=int(match.group(1)),head=target['oid']))
        if not page['hasNextPage']:
            need(len(rows)==total,'guard_discovery_changed')
            return rows
        cursor=page.get('endCursor')
        need(nodes and isinstance(cursor,str) and cursor and cursor not in seen_cursors
             and len(rows)<total,'guard_discovery_unknown')
        seen_cursors.add(cursor);after=cursor


def inspect(api,repo,policy):
    """Keep every per-ref failure visible; this does not authorize recovery."""
    rows=list_refs(api,repo);results=[]
    for row in rows:
        try:
            state=provider.read_chain(api,repo,policy['genesis'],policy['ruleset_id'],row['issue'])
            need(state['head']==row['head'],'guard_discovery_changed')
            claim=state['state']['locks'].get(str(row['issue']))
            if claim is None:
                results.append(dict(issue=row['issue'],state='unheld'))
            elif claim['kind']=='worker':
                results.append(dict(issue=row['issue'],state='worker_held'))
            else:
                owner=claim['owner']
                need(len(owner)==56,'guard_discovery_owner')
                run=int(owner[:16],16);attempt=int(owner[16:24],16)
                need(run>0 and attempt>0,'guard_discovery_owner')
                results.append(dict(issue=row['issue'],state='merge_held',head=claim['head'],
                                    owner=owner,run=run,attempt=attempt))
        except Exception as error:
            reason=str(error)
            if not isinstance(error,provider.Refused) or not re.fullmatch(r'[a-z0-9_]+',reason):
                reason='guard_discovery_failed'
            results.append(dict(issue=row['issue'],state='held',reason=reason))
    return results
