"""Paginated discovery controls; no GitHub refs are created."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/guard_discovery.py'
spec=importlib.util.spec_from_file_location('guard_discovery_draft',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class Source:
    def __init__(self,count):self.count=count;self.bad=None;self.calls=[]
    def api(self,path,method='GET',data=None):
        assert path=='graphql' and method=='POST' and data['query']==m.QUERY
        assert data['variables']['owner']=='o' and data['variables']['name']=='r'
        after=data['variables']['after'];self.calls.append(after)
        offset=int(after) if after else 0
        nodes=[dict(name=f'aw-issue-guard/issue-{i}',target=dict(__typename='Commit',oid='a'*40))
               for i in range(offset+1,min(offset+101,self.count+1))]
        if self.bad=='duplicate' and after:nodes[0]['name']='aw-issue-guard/issue-1'
        if self.bad=='incomplete' and after:nodes=nodes[:-1]
        count=self.count+(1 if self.bad=='changed' and after else 0)
        more=offset+100<self.count
        return dict(data=dict(repository=dict(nameWithOwner='o/r',refs=dict(totalCount=count,nodes=nodes,
            pageInfo=dict(hasNextPage=more,endCursor=str(offset+100) if more else None)))))


class DiscoveryTests(unittest.TestCase):
    def test_all_pages_and_empty_list(self):
        source=Source(205);rows=m.list_refs(source,'o/r')
        self.assertEqual(len(rows),205);self.assertEqual(source.calls,[None,'100','200'])
        self.assertEqual(m.list_refs(Source(0),'o/r'),[])

    def test_duplicate_missing_or_changed_page_is_a_hold(self):
        for bad in ('duplicate','incomplete','changed'):
            source=Source(205);source.bad=bad
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):m.list_refs(source,'o/r')

    def test_one_bad_ref_does_not_drop_other_candidates(self):
        source=Source(3);owner=f'{123:016x}{2:08x}'+'a'*32
        def read(api,repo,genesis,ruleset,issue):
            if issue==2:raise m.provider.Refused('guard_history_unknown')
            claim=None if issue==1 else dict(kind='merge',owner=owner,head='b'*40)
            return dict(head='a'*40,state=dict(locks={} if claim is None else {str(issue):claim}))
        with patch.object(m.provider,'read_chain',side_effect=read):
            rows=m.inspect(source,'o/r',dict(genesis='c'*40,ruleset_id=9))
        self.assertEqual([row['state'] for row in rows],['unheld','held','merge_held'])
        self.assertEqual((rows[2]['run'],rows[2]['attempt']),(123,2))
        self.assertEqual(rows[1]['reason'],'guard_history_unknown')


if __name__=='__main__':unittest.main()
