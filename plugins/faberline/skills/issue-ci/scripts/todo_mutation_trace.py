"""Bind issue outcome mutations to a guard and record unanswered requests.

The trace is evidence only.  A recovery writer must authenticate the source
job and guard before using it.  A missing response is always unknown.
"""
import hashlib
import json
import re

MARKER='AW_TODO_MUTATION_TRACE_V1 '
SEAL_MARKER='AW_TODO_MUTATION_SEAL_V1 '
SHA=re.compile(r'[0-9a-f]{64}')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition,reason):
    if not condition:
        raise RuntimeError(reason)


class TracedApi:
    """Allow only the exact outcome writer's status and issue mutations."""
    def __init__(self,api,repo,issue,head,intent_sha,emit):
        require(isinstance(repo,str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo)
                and type(issue) is int and issue>0 and isinstance(head,str)
                and re.fullmatch(r'[0-9a-f]{40}',head) and isinstance(intent_sha,str)
                and SHA.fullmatch(intent_sha) and callable(emit), 'todo_trace_identity')
        self.client=api;self.repo=repo;self.issue=issue;self.head=head
        self.intent_sha=intent_sha;self.emit=emit;self.sequence=0;self.sealed=False;self.comment_ids=set()

    def api(self,path,method='GET',data=None):
        if method=='GET':
            return self.client.api(path,method=method,data=data)
        require(not self.sealed,'todo_trace_sealed')
        root=f'repos/{self.repo}'
        allowed=(method=='POST' and path in (f'{root}/statuses/{self.head}',
                   f'{root}/issues/{self.issue}/comments',f'{root}/issues/{self.issue}/labels'))
        comment_match=(re.fullmatch(re.escape(root)+r'/issues/comments/([1-9][0-9]*)',path)
                       if isinstance(path,str) and method=='PATCH' else None)
        allowed=allowed or (comment_match is not None and int(comment_match.group(1)) in self.comment_ids)
        allowed=allowed or (method=='DELETE' and path in (f'{root}/issues/{self.issue}/labels/doing',
                                                          f'{root}/issues/{self.issue}/labels/done'))
        require(bool(allowed),'todo_trace_mutation_scope')
        self.sequence+=1
        request=digest([self.intent_sha,self.sequence,method,path,data])
        start=dict(version=1,intent_sha256=self.intent_sha,sequence=self.sequence,
                   phase='start',request_sha256=request,response_sha256=None)
        self.emit(MARKER+canonical(start))
        result=self.client.api(path,method=method,data=data)
        response=dict(start,phase='response',response_sha256=digest(result))
        self.emit(MARKER+canonical(response))
        if method=='POST' and path==f'{root}/issues/{self.issue}/comments':
            require(isinstance(result,dict) and type(result.get('id')) is int and result['id']>0,
                    'todo_trace_comment_unknown')
            self.comment_ids.add(result['id'])
        return result

    def pages(self,*args,**kwargs):
        result=self.client.pages(*args,**kwargs)
        path=args[0] if args else kwargs.get('path')
        if path==f'repos/{self.repo}/issues/{self.issue}/comments':
            require(isinstance(result,list),'todo_trace_comment_unknown')
            for item in result:
                if not isinstance(item,dict):
                    continue
                author=item.get('user')
                if (isinstance(author,dict) and author.get('login')=='github-actions[bot]'
                        and author.get('id')==41898282 and author.get('type')=='Bot'
                        and type(item.get('id')) is int and item['id']>0):
                    self.comment_ids.add(item['id'])
        return result

    def seal(self):
        """Mark that the whole todo operation returned, not just one request."""
        require(not self.sealed,'todo_trace_sealed')
        self.emit(SEAL_MARKER+canonical(dict(version=1,intent_sha256=self.intent_sha,operations=self.sequence)))
        self.sealed=True


def complete(records,intent_sha):
    """Return false for an unanswered last request; reject malformed traces."""
    require(isinstance(intent_sha,str) and SHA.fullmatch(intent_sha) and isinstance(records,list),
            'todo_trace_identity')
    pending=None;expected=1
    for item in records:
        require(isinstance(item,dict) and set(item)=={'version','intent_sha256','sequence','phase',
                'request_sha256','response_sha256'} and type(item['version']) is int and item['version']==1
                and item['intent_sha256']==intent_sha and type(item['sequence']) is int
                and item['sequence']==expected and isinstance(item['request_sha256'],str)
                and SHA.fullmatch(item['request_sha256']), 'todo_trace_record')
        if pending is None:
            require(item['phase']=='start' and item['response_sha256'] is None,'todo_trace_record')
            pending=item['request_sha256']
        else:
            require(item['phase']=='response' and item['request_sha256']==pending
                    and isinstance(item['response_sha256'],str) and SHA.fullmatch(item['response_sha256']),
                    'todo_trace_record')
            pending=None;expected+=1
    return pending is None
