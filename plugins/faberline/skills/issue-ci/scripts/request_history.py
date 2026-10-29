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
                 owner=p['owner'],request=identity,events=after['events'])
    return dict(payload=payload,sha256=hashlib.sha256(canonical(payload).encode()).hexdigest(),
                authorizes_merge=False)


def unchanged(value,current,pull,intent,policy,base):
    """Compare a later read. Callers must separately authenticate every source."""
    need(isinstance(value,dict) and set(value)=={'payload','sha256','authorizes_merge'}
         and value['authorizes_merge'] is False and isinstance(value['payload'],dict)
         and value['sha256']==hashlib.sha256(canonical(value['payload']).encode()).hexdigest(),
         'request_history_record')
    p=guard.validate(intent,policy);record=value['payload']
    need(set(record)=={'version','guard_sha256','repo','pr','issue','owner','request','events'}
         and type(record['version']) is int and record['version']==1
         and record['guard_sha256']==intent['sha256']
         and all(record[k]==p[k] for k in ('repo','pr','issue','owner'))
         and isinstance(current,dict) and current==dict(repo=p['repo'],pr=p['pr'],events=record['events'])
         and request_identity(pull,intent,policy,base)==record['request'], 'request_history_changed')
    history_shape(current,p['repo'],p['pr'])
    return dict(state='observed_unchanged',authorizes_merge=False)
