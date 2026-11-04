"""Validate a list from a previously authenticated Rust libtest harness.

Parsing output does not authenticate the process that emitted it. The caller
must establish the protected binary/image, isolate the list process from
untrusted product code, and bind each test execution to the same image.
"""
import re


def verify_roster(output, expected):
    """Require the full, nonzero reviewed suite roster from `--list` output."""
    if (not isinstance(output, str) or len(output.encode('utf-8')) > 1024 * 1024
            or not isinstance(expected, list) or not expected
            or any(not isinstance(name, str) or not name or len(name) > 512
                   or any(ord(char) < 32 or ord(char) == 127 for char in name) for name in expected)
            or len(set(expected)) != len(expected)):
        raise ValueError('reviewed nonzero test roster is required')
    lines = [line for line in output.splitlines() if line]
    if not lines:
        raise ValueError('test list is empty')
    summary = re.fullmatch(r'(\d+) tests?, (\d+) benchmarks?', lines[-1])
    if not summary or int(summary[1]) != len(expected) or int(summary[2]) != 0:
        raise ValueError('test list summary differs')
    names = []
    for line in lines[:-1]:
        if not line.endswith(': test'):
            raise ValueError('unexpected test list output')
        name = line[:-6]
        if not name or len(name) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError('invalid test name')
        names.append(name)
    if len(names) != len(expected) or len(set(names)) != len(names) or set(names) != set(expected):
        raise ValueError('test list differs from reviewed roster')
    return sorted(names)


def verify_gke_suite_rosters(all_output, ignored_output, expected):
    """Require both the full filtered suite and its `--ignored` selection.

    Lumen's existing GKE suites deliberately use ignored tests. Other suites
    may need a different execution contract; this function does not adopt one.
    """
    all_names = verify_roster(all_output, expected)
    selected_names = verify_roster(ignored_output, expected)
    if all_names != selected_names:
        raise ValueError('GKE test selection does not cover the full suite')
    return all_names


def verify_suite_execution(output, exit_code, expected):
    """Reject a successful libtest process that skipped or added tests.

    Output and exit code must come from the authenticated protected runner;
    this parser does not authenticate either one or prove Job provenance.
    """
    if type(exit_code) is not int or exit_code != 0 or not isinstance(output, str) or len(output.encode()) > 1024 * 1024:
        raise ValueError('trusted exact test process did not succeed')
    if not isinstance(expected, list) or not expected:
        raise ValueError('reviewed nonzero test roster is required')
    # Reuse roster validation for identities, duplicates and control chars.
    synthetic = ''.join(name + ': test\n' for name in expected if isinstance(name, str))
    verify_roster(synthetic + f'\n{len(expected)} tests, 0 benchmarks\n', expected)
    lines = [line for line in output.splitlines() if line]
    count = len(expected)
    if (len(lines) != count + 2
            or not re.fullmatch(r'running ' + str(count) + r' tests?', lines[0])
            or not re.fullmatch(r'test result: ok\. ' + str(count) + r' passed; 0 failed; 0 ignored; 0 measured; '
                                r'[0-9]+ filtered out; finished in [0-9]+(?:\.[0-9]+)?s', lines[-1])):
        raise ValueError('suite result is missing, skipped or ambiguous')
    required = {'test ' + name + ' ... ok' for name in expected}
    if len(required) != count or len(set(lines[1:-1])) != count or set(lines[1:-1]) != required:
        raise ValueError('suite results differ from reviewed roster')
    return sorted(expected)
