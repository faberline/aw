"""Run a reviewed test harness on an isolated GitHub-hosted runner.

This module does not authenticate its inputs or publish evidence. Before it
is used, a trusted controller must authenticate the binary's provenance,
protect this entire source closure, establish the port-forward and credentials,
bind the reviewed roster and environment, and recheck PR head/base. Product
PR commands must never execute on this runner.
"""
import importlib.util
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

spec = importlib.util.spec_from_file_location('aw_trusted_roster', Path(__file__).with_name('trusted_roster.py'))
roster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roster)

ALLOWED_ENV = {'PATH', 'HOME', 'KUBECONFIG', 'E2E_ENDPOINT',
               'E2E_NAMESPACE', 'E2E_POD_NAME', 'E2E_DOC_COUNT', 'E2E_EXPECTED_GIT_SHA',
               'E2E_EXPECTED_IMAGE', 'E2E_EXPECTED_RUNTIME_DIGEST', 'E2E_EXPECTED_VERSION'}
REQUIRED_ENV = {'PATH', 'HOME', 'KUBECONFIG', 'E2E_ENDPOINT', 'E2E_NAMESPACE', 'E2E_POD_NAME',
                'E2E_DOC_COUNT', 'E2E_EXPECTED_GIT_SHA', 'E2E_EXPECTED_IMAGE',
                'E2E_EXPECTED_RUNTIME_DIGEST', 'E2E_EXPECTED_VERSION'}
def run_suite(image, suite_filter, expected, environment, fixed_environment, *, sidecar_ip, gateway_port, timeout, execute):
    """Run full and ignored lists, then the original whole ignored suite.

    The caller supplies only independently authenticated policy and immutable
    image identity. `execute` must run that exact image for each process in an independently terminated
    container, bound output without streaming to Actions logs, and enforce the
    timeout. Native subprocess/process-group cleanup is insufficient because
    a child can detach. Results are not CI acceptance receipts.
    """
    if (not isinstance(image, str)
            or not re.fullmatch(r'ghcr\.io/faberline/[a-z0-9._-]+@sha256:[0-9a-f]{64}', image)
            or not isinstance(suite_filter, str) or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,256}', suite_filter)
            or not isinstance(expected, list) or not expected
            or any(not isinstance(name, str) or not name.startswith(suite_filter) for name in expected)
            or not isinstance(environment, dict) or not REQUIRED_ENV <= set(environment) <= ALLOWED_ENV
            or not isinstance(fixed_environment, dict) or 'E2E_DOC_COUNT' not in fixed_environment
            or any(key not in environment or environment[key] != value for key, value in fixed_environment.items())
            or any(not isinstance(value, str) or '\0' in value or '\n' in value for value in environment.values())
            or type(timeout) is not int or not 0 < timeout <= 5400
            or type(gateway_port) is not int or not 0 < gateway_port < 65536):
        raise ValueError('reviewed suite, immutable image and environment are required')
    if (not all(Path(environment[key]).is_absolute() for key in ('HOME', 'KUBECONFIG'))
            or not all(Path(path).is_absolute() for path in environment['PATH'].split(':'))):
        raise ValueError('protected runner paths must be absolute')
    try:
        endpoint = urlsplit(environment['E2E_ENDPOINT'])
        address = ipaddress.ip_address(sidecar_ip)
        private = any(address in ipaddress.ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        valid_endpoint = (address.version == 4 and private
                          and endpoint.scheme == 'http' and endpoint.hostname == str(address)
                          and endpoint.port == gateway_port
                          and endpoint.username is None and endpoint.password is None
                          and not endpoint.path and not endpoint.query and not endpoint.fragment)
    except (ValueError, TypeError):
        valid_endpoint = False
    if not valid_endpoint:
        raise ValueError('the inspected protected sidecar endpoint is required')
    binary = '/usr/local/bin/lumen-it'
    minimal = {'PATH': environment['PATH'], 'HOME': environment['HOME'], 'LC_ALL': 'C'}
    code, all_output = execute(image, [binary, suite_filter, '--list'], minimal, timeout)
    if code != 0: raise ValueError('protected full test list failed')
    code, selected_output = execute(image, [binary, suite_filter, '--ignored', '--list'], minimal, timeout)
    if code != 0: raise ValueError('protected selected test list failed')
    roster.verify_gke_suite_rosters(all_output, selected_output, expected)
    code, output = execute(image, [binary, '--ignored', '--test-threads=1', suite_filter],
                           dict(environment, LC_ALL='C'), timeout)
    result = roster.verify_suite_execution(output, code, expected)
    return {'test_image': image, 'suite_filter': suite_filter, 'tests': result}
