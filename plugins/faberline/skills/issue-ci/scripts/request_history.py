"""Draft read-only evidence for a native request. It never authorizes writes.

The caller must authenticate the writer, its mutation response and the guard.
Two matching timeline reads do not prove GitHub has delivered every event.
This evidence is not a request owner, merge or issue-close authorization.
"""
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import re

_spec=importlib.util.spec_from_file_location('aw_request_history_guard',Path(__file__).with_name('merge_guard.py'))
guard=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(guard)
need=guard.need
MAX_ITEMS=10000
TYPES=('AutoSquashEnabledEvent','AutoRebaseEnabledEvent','AutoMergeEnabledEvent','AutoMergeDisabledEvent')
MARKER='AW_REQUEST_HISTORY_OBSERVATION_V1 '
ACK_MARKER='AW_REQUEST_MUTATION_ACK_V1 '
QUERY='''query($owner:String!,$name:String!,$number:Int!,$after:String){
  repository(owner:$owner,name:$name){nameWithOwner pullRequest(number:$number){number
    timelineItems(first:100,after:$after){totalCount nodes{__typename
      ... on AutoSquashEnabledEvent{id createdAt actor{__typename login}}
      ... on AutoRebaseEnabledEvent{id createdAt actor{__typename login}}
      ... on AutoMergeEnabledEvent{id createdAt actor{__typename login}}
      ... on AutoMergeDisabledEvent{id createdAt actor{__typename login}}
    } pageInfo{hasNextPage endCursor}}
  }}
}'''


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)


def history_shape(value,repo,number):
    need(isinstance(value,dict) and set(value)=={'repo','pr','events'}
         and value['repo']==repo and type(value['pr']) is int and value['pr']==number
         and isinstance(value['events'],list) and len(value['events'])<=MAX_ITEMS,
         'request_history_unknown')
    ids=set()
    for event in value['events']:
        need(isinstance(event,dict) and set(event)=={'type','id','created_at','actor_type','actor_login'}
             and event['type'] in TYPES and isinstance(event['id'],str) and 0<len(event['id'])<=200
             and event['id'] not in ids and isinstance(event['created_at'],str)
             and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',event['created_at'])
             and event['actor_type'] in ('Bot','User','Organization')
             and isinstance(event['actor_login'],str) and 0<len(event['actor_login'])<=100,
             'request_history_unknown')
        try:datetime.strptime(event['created_at'],'%Y-%m-%dT%H:%M:%SZ')
        except ValueError:raise guard.provider.Refused('request_history_unknown') from None
        ids.add(event['id'])
    return value


def snapshot(api,repo,number):
    """Read the full timeline, not only a filtered page, or refuse it."""
    need(isinstance(repo,str) and guard.provider.ledger.REPO.fullmatch(repo)
         and type(number) is int and 0<number<2**53,'request_history_identity')
    owner,name=repo.split('/');after=None;cursors=set();events=[];ids=set();count=0;total=None
    while True:
        value=guard.provider.graphql(api,QUERY,dict(owner=owner,name=name,number=number,after=after))
        root=value.get('repository') if isinstance(value,dict) else None
        pull=root.get('pullRequest') if isinstance(root,dict) else None
        page=pull.get('timelineItems') if isinstance(pull,dict) else None
        need(isinstance(root,dict) and root.get('nameWithOwner')==repo
             and isinstance(pull,dict) and pull.get('number')==number and isinstance(page,dict),
             'request_history_unknown')
        nodes=page.get('nodes');info=page.get('pageInfo');size=page.get('totalCount')
        need(type(size) is int and 0<=size<=MAX_ITEMS and isinstance(nodes,list) and len(nodes)<=100
             and isinstance(info,dict) and type(info.get('hasNextPage')) is bool,'request_history_unknown')
        if total is None:total=size
        need(total==size,'request_history_changed')
        for node in nodes:
            need(isinstance(node,dict) and isinstance(node.get('__typename'),str)
                 and node['__typename'],'request_history_unknown')
            if node['__typename'] not in TYPES:continue
            actor=node.get('actor');event_id=node.get('id');created=node.get('createdAt')
            need(isinstance(event_id,str) and 0<len(event_id)<=200 and event_id not in ids
                 and isinstance(created,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',created)
                 and isinstance(actor,dict) and actor.get('__typename') in ('Bot','User','Organization')
                 and isinstance(actor.get('login'),str) and 0<len(actor['login'])<=100,
                 'request_history_unknown')
            try:datetime.strptime(created,'%Y-%m-%dT%H:%M:%SZ')
            except ValueError:raise guard.provider.Refused('request_history_unknown') from None
            ids.add(event_id)
            events.append(dict(type=node['__typename'],id=event_id,created_at=created,
                               actor_type=actor['__typename'],actor_login=actor['login']))
        count+=len(nodes);need(count<=total,'request_history_changed')
        if not info['hasNextPage']:
            need(count==total,'request_history_changed')
            return history_shape(dict(repo=repo,pr=number,events=events),repo,number)
        cursor=info.get('endCursor')
        need(bool(nodes) and count<total and isinstance(cursor,str) and cursor and cursor not in cursors,
             'request_history_unknown')
        cursors.add(cursor);after=cursor


def stable(api,repo,number):
    first=snapshot(api,repo,number);second=snapshot(api,repo,number)
    need(first==second,'request_history_changed')
    return first


def request_identity(pull,intent,policy,base):
    p=guard.validate(intent,policy)
    need(isinstance(base,str) and guard.provider.ledger.SHA.fullmatch(base)
         and isinstance(pull,dict) and pull.get('state')=='OPEN' and pull.get('headRefOid')==p['head']
         and pull.get('baseRefOid')==base and pull.get('baseRefName')=='main','request_history_target')
    request=pull.get('autoMergeRequest');actor=request.get('enabledBy') if isinstance(request,dict) else None
    need(isinstance(request,dict) and request.get('mergeMethod')=='SQUASH'
         and request.get('commitHeadline')==f"Merge PR #{p['pr']}"
         and request.get('commitBody')==f"Closes #{p['issue']}"
         and isinstance(actor,dict) and actor.get('__typename')=='Bot' and actor.get('login')=='github-actions'
         and isinstance(request.get('enabledAt'),str)
         and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',request['enabledAt']),
         'request_history_target')
    return dict(head=p['head'],base=base,enabled_at=request['enabledAt'],
                headline=request['commitHeadline'],body=request['commitBody'])


def bind(before,after,pull,intent,policy,base):
    """Bind one observed enable event to a guard; this does not prove authorship."""
    p=guard.validate(intent,policy)
    history_shape(before,p['repo'],p['pr']);history_shape(after,p['repo'],p['pr'])
    need(len(after['events'])==len(before['events'])+1
         and after['events'][:-1]==before['events'],'request_history_changed')
    event=after['events'][-1];identity=request_identity(pull,intent,policy,base)
    need(event.get('type')=='AutoSquashEnabledEvent' and event.get('actor_type')=='Bot'
         and event.get('actor_login')=='github-actions' and event.get('created_at')==identity['enabled_at'],
         'request_history_target')
    payload=dict(version=1,guard_sha256=intent['sha256'],repo=p['repo'],pr=p['pr'],issue=p['issue'],
                 owner=p['owner'],request=identity,events=after['events'],
                 previous_events_sha256=hashlib.sha256(canonical(before['events']).encode()).hexdigest())
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest(),
                authorizes_merge=False)


def unchanged(value,current,pull,intent,policy,base):
    """Compare a later read. Callers must separately authenticate every source."""
    need(isinstance(value,dict) and set(value)=={'payload','sha256','authorizes_merge'}
         and value['authorizes_merge'] is False and isinstance(value['payload'],dict)
         and value['sha256']==hashlib.sha256(canonical(value['payload']).encode()).hexdigest(),
         'request_history_record')
    p=guard.validate(intent,policy);record=value['payload']
    need(set(record)=={'version','guard_sha256','repo','pr','issue','owner','request','events',
                       'previous_events_sha256'}
         and type(record['version']) is int and record['version']==1
         and record['guard_sha256']==intent['sha256']
         and all(record[k]==p[k] for k in ('repo','pr','issue','owner'))
         and isinstance(current,dict) and current==dict(repo=p['repo'],pr=p['pr'],events=record['events'])
         and request_identity(pull,intent,policy,base)==record['request'], 'request_history_changed')
    history_shape(current,p['repo'],p['pr'])
    events=current['events']
    need(bool(events) and isinstance(record['previous_events_sha256'],str)
         and record['previous_events_sha256']==hashlib.sha256(canonical(events[:-1]).encode()).hexdigest()
         and events[-1]['type']=='AutoSquashEnabledEvent' and events[-1]['actor_type']=='Bot'
         and events[-1]['actor_login']=='github-actions'
         and events[-1]['created_at']==record['request']['enabled_at'],'request_history_changed')
    return dict(state='observed_unchanged',authorizes_merge=False)


def acknowledgement(response,observation,intent,policy):
    """Bind a returned client ID to this guard without authorizing a write.

    GitHub echoes clientMutationId in the mutation response, not in a later
    auto-merge request query. The caller must authenticate the producer log.
    This observation alone cannot establish request authorship or prevent a
    later replacement, and does not authorize merge, cancellation or close.
    """
    p=guard.validate(intent,policy)
    need(isinstance(response,dict) and response.get('clientMutationId')==intent['sha256'],
         'request_ack_unknown')
    need(isinstance(observation,dict) and set(observation)=={'payload','sha256','authorizes_merge'}
         and observation['authorizes_merge'] is False and isinstance(observation['payload'],dict)
         and observation['sha256']==hashlib.sha256(canonical(observation['payload']).encode()).hexdigest()
         and observation['payload'].get('guard_sha256')==intent['sha256']
         and all(observation['payload'].get(k)==p[k] for k in ('repo','pr','issue','owner')),
         'request_ack_unknown')
    payload=dict(version=1,repo=p['repo'],pr=p['pr'],head=p['head'],issue=p['issue'],owner=p['owner'],
                 run=p['run'],attempt=p['attempt'],guard_sha256=intent['sha256'],
                 client_mutation_id=response['clientMutationId'],observation_sha256=observation['sha256'])
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest(),
                authorizes_merge=False)


def find_acknowledgement(log,observation,intent,policy):
    """Find one bound ACK in a log; the caller must authenticate that log."""
    p=guard.validate(intent,policy);matches=[]
    for value in guard.review.r.records(log,ACK_MARKER):
        need(isinstance(value,dict) and set(value)=={'payload','sha256','authorizes_merge'}
             and value['authorizes_merge'] is False and isinstance(value['payload'],dict)
             and value['sha256']==hashlib.sha256(canonical(value['payload']).encode()).hexdigest(),
             'request_ack_unknown')
        row=value['payload']
        need(set(row)=={'version','repo','pr','head','issue','owner','run','attempt','guard_sha256',
                        'client_mutation_id','observation_sha256'} and type(row['version']) is int
             and row['version']==1 and isinstance(row['repo'],str)
             and guard.provider.ledger.REPO.fullmatch(row['repo'])
             and all(type(row[k]) is int and 0<row[k]<2**53 for k in ('pr','issue'))
             and type(row['run']) is int and 0<row['run']<2**64
             and type(row['attempt']) is int and 0<row['attempt']<2**32
             and isinstance(row['head'],str) and guard.provider.ledger.SHA.fullmatch(row['head'])
             and isinstance(row['owner'],str) and len(row['owner'])==56
             and guard.provider.ledger.OWNER.fullmatch(row['owner'])
             and all(isinstance(row[k],str) and re.fullmatch(r'[0-9a-f]{64}',row[k])
                     for k in ('guard_sha256','client_mutation_id','observation_sha256'))
             and row['client_mutation_id']==row['guard_sha256'],'request_ack_unknown')
        if (row.get('repo'),row.get('pr'),row.get('head'),row.get('owner'))==(p['repo'],p['pr'],p['head'],p['owner']):
            need(value==acknowledgement(dict(clientMutationId=row['client_mutation_id']),
                                         observation,intent,policy),'request_ack_unknown')
            matches.append(value)
    need(len(matches)<=1,'request_ack_duplicate')
    return matches[0] if matches else None


def inspect_native_ack(api,repo,document,evaluator,run,attempt,pr,head,log_reader):
    """Authenticate source and compare its ACK with a currently held request.

    This read-only inspection still does not establish request authorship or
    authorize status, merge, cancellation, issue closure, or guard release.
    A complete stable history is not proof that the provider delivered every
    event, so an unchanged observation cannot itself be used as a lease.
    """
    policy,log=guard.authenticate_native_log(api,repo,document,evaluator,run,attempt,log_reader)
    intents=[]
    for value in guard.review.r.records(log,guard.MARKER):
        p=guard.validate(value,policy)
        need(p['repo']==repo and p['run']==run and p['attempt']==attempt,'request_ack_source')
        if p['pr']==pr and p['head']==head:intents.append(value)
    need(len(intents)==1,'request_ack_source')
    intent=intents[0];p=intent['payload']
    observations=[]
    for value in guard.review.r.records(log,MARKER):
        need(isinstance(value,dict) and isinstance(value.get('payload'),dict),'request_ack_source')
        if value['payload'].get('guard_sha256')==intent['sha256']:observations.append(value)
    need(len(observations)==1,'request_ack_source')
    observation=observations[0]
    ack=find_acknowledgement(log,observation,intent,policy)
    need(ack is not None,'request_ack_missing')
    current=guard.worker.read_policy(api,repo)
    need(current is not None,'request_ack_policy')
    guard.worker.config(current);guard.worker.check_runtime(current)
    need(all(current[k]==policy[k] for k in ('genesis','ruleset_id','runtime')),'request_ack_policy')
    closer=guard.review.sibling('guarded_close');native=guard.review.sibling('native_merge')
    def check():
        closer.require_current_policy(api,repo,current)
        closer.guard_identity(api,repo,current,intent)
        pull=native.provider_pull(api,repo,pr)
        history=stable(api,repo,pr)
        base=observation['payload'].get('request',{}).get('base')
        unchanged(observation,history,pull,intent,policy,base)
        closer.require_current_policy(api,repo,current)
        closer.guard_identity(api,repo,current,intent)
        return pull,history
    first=check();second=check()
    need(first==second,'request_history_changed')
    return dict(repo=repo,pr=pr,head=head,issue=p['issue'],owner=p['owner'],run=run,attempt=attempt,
                acknowledgement_sha256=ack['sha256'],state='observed_unchanged',authorizes_merge=False)
