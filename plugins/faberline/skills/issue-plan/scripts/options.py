"""Portable skill input parser. Never invokes a skill, timer or provider write."""
import argparse
import json
from pathlib import Path
import re

TASKS = ('triage', 'queue', 'gaps', 'dependencies', 'release')


def parse(argv, config=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--tasks')
    parser.add_argument('--milestone', type=int)
    parser.add_argument('--role', choices=['product', 'shared'])
    args = parser.parse_args(argv)
    config = {} if config is None else config
    if not isinstance(config, dict) or set(config) - {'version', 'role', 'tasks', 'milestone'}:
        raise ValueError('invalid planner configuration')
    if config and (type(config.get('version')) is not int or config['version'] != 1):
        raise ValueError('planner configuration needs version 1')
    role = args.role or config.get('role', 'product')
    if role not in ('product', 'shared'):
        raise ValueError('invalid planner role')
    requested = args.tasks.split(',') if args.tasks is not None else config.get(
        'tasks', list(TASKS) if role == 'product' else ['triage', 'queue', 'release'])
    if (not isinstance(requested, list) or not requested or any(t not in TASKS for t in requested)
            or len(requested) != len(set(requested))):
        raise ValueError('tasks must be a nonempty, unique subset of ' + ','.join(TASKS))
    milestone = args.milestone if args.milestone is not None else config.get('milestone')
    if milestone is not None and (type(milestone) is not int or milestone < 1):
        raise ValueError('milestone must be an existing positive issue-milestone number')
    return dict(schema='AW_PLANNER_OPTIONS_V1', role=role, tasks=[t for t in TASKS if t in requested],
                milestone=milestone, creates_milestones=False, invents_product_capability=False,
                mutation_authorized=False)


def resolve(argv, path=Path('.github/issue-plan.json')):
    # Validate all supplied arguments before even reading a configuration file.
    parse(argv)
    return parse(argv, json.loads(path.read_text()) if path.exists() else None)
