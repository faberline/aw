#!/usr/bin/env python3
"""Build portable planner context, hashes, and six two-hour task prompts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPOS = ('core', 'defer', 'keep', 'lumen', 'relay', 'tape')
FILES = ('issue-plan/SKILL.md', 'issue-plan/CLOUD.md', 'issue-plan/scripts/plan.py',
         'issue-plan/scripts/plan.sh', 'issue-plan/scripts/bundle.py',
         'issue-plan/scripts/connector.py',
         'issue-plan/scripts/native_transport.py',
         'issue-loop/MILESTONE-QUEUE.md', 'issue-loop/scripts/milestone_queue.py',
         'issue-loop/scripts/github_guard.py')


def build(destination, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('source revision must be a published immutable 40-character commit')
    destination = Path(destination)
    if destination.exists():
        raise ValueError('bundle destination already exists; preserve it')
    skills = Path(__file__).resolve().parents[2]
    contents = {name: (skills / name).read_bytes() for name in FILES}
    root = skills.parents[2]
    for name, data in contents.items():
        committed = subprocess.check_output(['git', '-C', str(root), 'show',
                            revision + ':plugins/faberline/skills/' + name])
        if data != committed:
            raise ValueError(f'working source differs from pinned commit: {name}')
    manifest = dict(schema=1, source_repository='faberline/aw', source_revision=revision,
                    interval_hours=2, files={name: hashlib.sha256(data).hexdigest()
                                             for name, data in contents.items()})
    destination.mkdir(parents=True)
    for name, data in contents.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    prompts = {}
    for repo in REPOS:
        prompts[repo] = (
            f'Plan faberline/{repo} once per activation, every two hours, in OpenAI hosted execution. '
            'Do not use local-computer access. Load the portable issue-plan skill and CLOUD.md '
            f'from faberline/aw commit {revision}. Verify every bundled file against this manifest: '
            + json.dumps(manifest['files'], sort_keys=True) + '. '
            'Follow SKILL.md, CLOUD.md and MILESTONE-QUEUE.md, including the complete discussions, '
            'numeric SemVer frontier, backlog-first queue, existing holds and dependencies. '
            f'Target faberline/{repo}. Prefer the native GitHub connector with connector.run '
            'and NativeTransport(repo, call_tool, connector.queue.coordination) '
            'and the unchanged deterministic policy; no product checkout or shell gh login is required. '
            'Use the guarded shell adapter only if that runtime already has gh authorization. '
            'Require verified shared-guard adoption by the current local writers. Missing or '
            'incomplete source, tools, authorization, peer or guard evidence blocks mutation. '
            'An idle/offline historical registration alone proves neither active overlap nor '
            'lease expiry. Never steal ownership, clear a guard, overwrite a human issue body, '
            'close issues, implement product code, run product tests, dispatch CI, review or '
            'merge product PRs, change releases/security/costs, or start another schedule. '
            'Preserve protected ownership, including Lumen #45. Report actual changed issue '
            'links, or nothing to do with the exact reason. Stay quiet when nothing changes '
            'and no action is needed; report meaningful changes, failures or required user action.'
        )
    (destination / 'prompts.json').write_text(json.dumps(prompts, indent=2) + '\n')
    return manifest


def verify(destination):
    destination = Path(destination)
    manifest = json.loads((destination / 'manifest.json').read_text())
    if (manifest.get('schema') != 1 or set(manifest.get('files', {})) != set(FILES)
            or manifest.get('interval_hours') != 2
            or manifest.get('source_repository') != 'faberline/aw'
            or not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_revision', ''))):
        raise ValueError('invalid bundle manifest')
    for name, expected in manifest['files'].items():
        path = destination / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'bundle hash mismatch: {name}')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['build', 'verify'])
    parser.add_argument('destination', type=Path)
    parser.add_argument('--revision')
    args = parser.parse_args()
    result = build(args.destination, args.revision or '') if args.command == 'build' else verify(args.destination)
    print(json.dumps(result, sort_keys=True))
