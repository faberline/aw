"""Record the actual clean control checkout after the complete suite passed."""
import json
import os
from pathlib import Path
import re
import subprocess


def capture(root, repository):
    if repository != 'faberline/aw':
        raise RuntimeError('control source repository')
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git('status','--porcelain=v1','--untracked-files=all','--ignore-submodules=none'):
        raise RuntimeError('control source changed during tests')
    commit,tree=git('rev-parse','--verify','HEAD'),git('rev-parse','--verify','HEAD^{tree}')
    if any(re.fullmatch('[0-9a-f]{40}',value) is None for value in (commit,tree)):
        raise RuntimeError('control source identity')
    return dict(version=1,repo=repository,commit=commit,tree=tree)


if __name__=='__main__':
    if os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('control source requires the hosted control job')
    print('AW_CONTROL_SOURCE_V1 '+json.dumps(capture(Path.cwd(),os.environ.get('GITHUB_REPOSITORY')),sort_keys=True))
