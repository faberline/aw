"""Shared queue contract. Read-only selection; no lifecycle/release inference.

All GitHub lists use explicit pages. Unsupported dependency reads fail closed.
Only explicit incoming prose dependencies are interpreted; never reverse `blocks`.
"""
import hashlib
import json
import re
import subprocess

STATES = {'todo', 'doing', 'done', 'blocked'}
SPEC_MARK = '<!-- issue-plan:spec -->'
VERSION = re.compile(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?'
                     r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                     r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z')
REFERENCE = re.compile(r'https://github\.com/([\w.-]+/[\w.-]+)/(issues|pull)/(\d+)'
                       r'|(?<![\w/])([\w.-]+/[\w.-]+)#(\d+)'
                       r'|(?<![\w/#])#(\d+)')
CUE = re.compile(r'\b(?:depends?\s+on|blocked\s+(?:by|on)|waits?\s+for)\b|'
                 r'\b(?:blocking\s+dependenc(?:y|ies)|prerequisites?|dependenc(?:y|ies))\s*:|'
                 r'(?:前置依賴|依賴|前置條件|先決條件)\s*[:：]', re.I)
DEPENDENCY_HEADING = re.compile(r'^\s*#{1,6}\s+(?:dependencies|prerequisites?|前置依賴|依賴|前置條件|先決條件)\s*[:：]?\s*$', re.I)
BARRIER = re.compile(r'(?:stays?|remains?)\s+open[^.\n]*?\buntil\s+|不能完成[^。\n]*?直到', re.I)
OUTGOING = re.compile(r'^\s*(?:[-*]\s*)?(?:blocks|unblocks)\b', re.I)


def version(title, repo):
    if not isinstance(title, str):
        raise ValueError('missing milestone title')
    if '@' in title:
        prefix, title = title.split('@', 1)
        if prefix not in (repo, repo.split('/')[-1]):
            raise ValueError('milestone prefix does not match repository')
    match = VERSION.fullmatch(title)
    if not match:
        raise ValueError('milestone must be [repo@][v]MAJOR.MINOR[.PATCH][-prerelease][+build]')
    major, minor, patch, pre, _ = match.groups()
    parts = []
    for part in pre.split('.') if pre else []:
        if part.isdigit() and len(part) > 1 and part.startswith('0'):
            raise ValueError('leading zero in prerelease')
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    # Release sorts after any prerelease; build metadata has no precedence.
    return int(major), int(minor), int(patch or 0), (1,) if pre is None else (0, tuple(parts))


def labels(issue):
    return {label if isinstance(label, str) else label['name'] for label in issue['labels']}


def priority(issue):
    values = [int(v[-1]) for v in labels(issue) if re.fullmatch(r'priority:p[0-3]', v)]
    return min(values, default=2), issue['created_at'], issue['number']


def milestone_version(issue, repo, milestones):
    assigned = issue.get('milestone')
    if not assigned:
        raise ValueError('missing milestone')
    m = milestones.get(assigned['number'])
    if not m:
        raise ValueError('assigned milestone absent from complete catalog')
    if m['state'] != 'open':
        raise ValueError('assigned milestone is closed')
    return version(m['title'], repo)


def prose_dependencies(text, repo):
    """Conservative line/section grammar, not an NLP or relationship importer.

    Dependency headings include their list until the next heading/blank line.
    A declaration without resolvable refs is unknown, not permission to proceed.
    Code fences are examples. `blocks #N` is outgoing and never reversed.
    """
    refs, unknown, section, fenced = set(), [], False, False
    for line in text.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced:
            continue
        barrier = BARRIER.search(line)
        cue = barrier or CUE.search(line) or DEPENDENCY_HEADING.fullmatch(line)
        if line.startswith('#') or not line.strip():
            section = False
        if OUTGOING.search(line):
            continue
        if not cue and section and not re.match(r'^\s*[-*]\s+', line):
            section = False
        if not cue and not section:
            continue
        # Markdown link labels are not independent issue references.
        declaration = line[barrier.end():] if barrier else line
        source = re.sub(r'\[[^\]]*\]\((https://github\.com/[^)]+)\)', r'\1', declaration)
        found = list(REFERENCE.finditer(source))
        for m in found:
            target_repo = m[1] or m[4] or repo
            kind = m[2] or 'issues'
            number = int(m[3] or m[5] or m[6])
            refs.add((target_repo.lower(), kind, number))
        heading = bool(cue and re.fullmatch(r'\s*#{1,6}\s+[^#]+', line))
        explicit_none = bool(re.fullmatch(r'\s*(?:[-*]\s*)?(?:(?:dependencies|prerequisites?)\s*:\s*)?(?:none|無)\.?\s*', line, re.I))
        if (cue or section) and not found and not heading and not explicit_none:
            unknown.append(line.strip())
        if cue:
            section = heading or line.rstrip().endswith(':')
    return sorted(refs), unknown


def select(repo, issues, milestones, blockers):
    """Frontier is earliest version containing OPEN issues, including review waits.

    Empty milestones are ignored. Metadata errors block new claims globally.
    Dependencies only affect their issue; no future batch leap when frontier waits.
    """
    catalog = {m['number']: m for m in milestones}
    versions, errors = {}, []
    for issue in issues:
        try:
            versions[issue['number']] = milestone_version(issue, repo, catalog)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"#{issue['number']}: {exc}")
    if errors:
        return dict(state='blocked', selected=None, frontier=None, reasons=sorted(errors), metadata_errors=sorted(errors))
    if not issues:
        return dict(state='idle', selected=None, frontier=None, reasons=[])
    frontier = min(versions.values())
    cohort = sorted((i for i in issues if versions[i['number']] == frontier), key=priority)
    reasons = []
    for issue in cohort:
        n, ls = issue['number'], labels(issue)
        if ls & STATES != {'todo'}:
            reasons.append(f"#{n}: lifecycle {','.join(sorted(ls & STATES)) or 'backlog'}")
            continue
        waits = blockers.get(n, [])
        if waits:
            reasons.extend(f'#{n}: {reason}' for reason in waits)
            continue
        return dict(state='ready', selected=n, frontier=list(frontier), reasons=reasons)
    return dict(state='blocked', selected=None, frontier=list(frontier), reasons=reasons)


class Client:
    """Normal gh authorization, public REST only, no token handling or edge writes."""
    def __init__(self, repo=None):
        self.repo = repo or self.command('repo', 'view', '--json', 'nameWithOwner')['nameWithOwner']
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', self.repo):
            raise ValueError('invalid repository')

    def command(self, *args, payload=None):
        cmd = ['gh', *args]
        if payload is not None:
            cmd += ['--input', '-']
        value = subprocess.check_output(cmd, input=None if payload is None else json.dumps(payload), text=True)
        return json.loads(value)

    def api(self, path):
        if not path.startswith('repos/') or '://' in path or '..' in path:
            raise ValueError('invalid API path')
        return self.command('api', path)

    def pages(self, path):
        result = []
        for page in range(1, 1001):
            rows = self.api(f'{path}{"&" if "?" in path else "?"}per_page=100&page={page}')
            if not isinstance(rows, list):
                raise ValueError('expected paginated array')
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError('pagination exceeds safety bound; no truncated selection')

    def issue(self, n, repo=None):
        return self.api(f'repos/{repo or self.repo}/issues/{int(n)}')

    def comments(self, n):
        return self.pages(f'repos/{self.repo}/issues/{int(n)}/comments')

    def spec(self, issue, comments):
        return next((c['body'] for c in reversed(comments) if c.get('body', '').startswith(SPEC_MARK)), issue.get('body') or '')

    def blockers(self, issue, catalog):
        n = issue['number']
        comments = self.comments(n)
        # Include the original body and comments: prose prerequisites are not erased
        # by a later spec or by an arbitrary "resolved" comment. Close/merge evidence
        # resolves referenced work; contradictory prose needs explicit triage.
        sources = [issue.get('body') or ''] + [c.get('body') or '' for c in comments]
        refs, reasons = set(), []
        for source in sources:
            parsed, unknown = prose_dependencies(source, self.repo)
            refs.update(parsed)
            reasons.extend(f'unresolved prose prerequisite: {v}' for v in unknown)
        for dep in self.pages(f'repos/{self.repo}/issues/{n}/dependencies/blocked_by'):
            url = dep.get('html_url', '')
            m = re.fullmatch(r'https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)', url)
            if not m:
                raise ValueError('unknown native dependency identity')
            refs.add((m[1].lower(), 'issues', int(m[2])))
        own_version = None
        try:
            own_version = milestone_version(issue, self.repo, catalog)
        except ValueError:
            pass  # selection reports the metadata error; continuity may still resume
        for repo, kind, number in sorted(refs):
            dep = self.issue(number, repo)
            if 'pull_request' in dep or kind == 'pull':
                pull = self.api(f'repos/{repo}/pulls/{number}')
                if pull.get('merged_at'):
                    continue
            elif dep['state'] == 'closed':
                continue
            message = f'waits for {repo}#{number} (not closed/merged)'
            if repo.lower() == self.repo.lower() and own_version is not None:
                try:
                    if milestone_version(dep, self.repo, catalog) > own_version:
                        message += '; dependency/version inversion: prerequisite is in a later batch'
                except ValueError:
                    message += '; prerequisite milestone invalid/missing/closed'
            reasons.append(message)
        return sorted(set(reasons))

    def snapshot(self, include=()):
        issues = [i for i in self.pages(f'repos/{self.repo}/issues?state=open') if 'pull_request' not in i and i['state'] == 'open']
        if len({i['number'] for i in issues}) != len(issues):
            raise ValueError('unstable paginated issue inventory; retry read')
        milestones = self.pages(f'repos/{self.repo}/milestones?state=all')
        catalog = {m['number']: m for m in milestones}
        # Every eligible todo needs dependencies checked, including future batches.
        # This also exposes inversions in status instead of silently deadlocking.
        waits = {i['number']: self.blockers(i, catalog) for i in issues if labels(i) & STATES == {'todo'} or i['number'] in include}
        return dict(repo=self.repo, issues=issues, milestones=milestones, blockers=waits,
                    decision=select(self.repo, issues, milestones, waits))

    def continuation(self, n):
        issue = self.issue(n)
        if issue['state'] != 'open' or labels(issue) & STATES not in ({'todo'}, {'doing'}):
            raise ValueError('owned/repair work is not exclusively open todo or doing; preserve lease/worktree')
        catalog = {m['number']: m for m in self.pages(f'repos/{self.repo}/milestones?state=all')}
        waits = self.blockers(issue, catalog)
        if waits:
            raise ValueError('; '.join(waits))
        return issue

    def patch(self, n, **payload):
        return self.command('api', '--method', 'PATCH', f'repos/{self.repo}/issues/{int(n)}', payload=payload)


def issue_identity(issue):
    # REST list and GET may contain different ancillary fields. Compare the
    # scheduling identity, not endpoint-specific response decoration.
    assigned = issue.get('milestone')
    return dict(number=issue['number'], state=issue['state'],
                labels=sorted(labels(issue)), title=issue.get('title'),
                body=issue.get('body'), updated_at=issue.get('updated_at'),
                created_at=issue.get('created_at'),
                milestone=None if not assigned else {k: assigned.get(k) for k in ('number', 'title', 'state')})


def milestone_identity(m):
    return {k: m.get(k) for k in ('number', 'title', 'state', 'updated_at')}


def fingerprint(snapshot):
    identity = dict(snapshot, issues=sorted((issue_identity(i) for i in snapshot['issues']), key=lambda i: i['number']),
                    milestones=sorted((milestone_identity(m) for m in snapshot['milestones']), key=lambda m: m['number']))
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
