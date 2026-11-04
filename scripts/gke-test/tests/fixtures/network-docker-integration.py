"""Exercise protected Docker network barriers on an ephemeral CI runner."""
import importlib.util
import json
from pathlib import Path
import re
import secrets
import select
import subprocess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DOCKER = '/usr/bin/docker'
IMAGE = 'docker.io/library/python@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144'
nonce = secrets.token_hex(16)
name = 'aw-protected-' + nonce
resources = []
network_id = None
stream = None
started = None
start_gate = None
fixture_tag = 'aw-protected-fixture-' + nonce + ':latest'
fixture_image = None
temporary = tempfile.TemporaryDirectory(prefix='aw-gke-fixture-')


def call(*args):
    return subprocess.run([DOCKER, *args], check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def identifier(value):
    if not re.fullmatch(r'[0-9a-f]{64}', value): raise ValueError('invalid Docker identity')
    return value


def load(module):
    spec = importlib.util.spec_from_file_location('integration_' + module, ROOT / (module + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def inspect_container(container_id):
    return json.loads(call('inspect', container_id))[0]


def create(role, network):
    try:
        container_id = identifier(call('create', '--pull=never', '--init', '--read-only',
            '--user=65532:65532', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--ipc=private', '--cgroupns=private', '--network=' + network,
            '--name=' + name + '-' + role, '--label=faberline.dev/protected-network=' + nonce,
            '--label=faberline.dev/protected-role=' + role, '--entrypoint=python', IMAGE,
            '-c', 'import time; time.sleep(120)'))
        resources.append((container_id, role))
        return container_id
    except BaseException as error:
        raise RuntimeError('container creation is uncertain; discard runner') from error


try:
    context = Path(temporary.name)
    binary = context / 'lumen-it'
    subprocess.run(['gcc', '-static', '-O2', str(ROOT / 'tests/fixtures/fake-lumen-it.c'),
                    '-o', str(binary)], check=True, capture_output=True, timeout=60)
    wrapper = context / 'trusted-start'
    subprocess.run(['gcc', '-static', '-O2', '-std=c11', '-D_POSIX_C_SOURCE=200809L',
                    str(ROOT / 'trusted_start.c'), '-o', str(wrapper)], check=True, capture_output=True, timeout=60)
    (context / 'Dockerfile').write_text('FROM scratch\nCOPY lumen-it /usr/local/bin/lumen-it\n'
                                        'ENV PATH=/usr/local/bin\nLABEL faberline.dev/protected-fixture=' + nonce + '\n')
    try:
        call('build', '--quiet', '--tag', fixture_tag, str(context))
        fixture_image = call('image', 'inspect', '--format', '{{.Id}}', fixture_tag)
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', fixture_image): raise ValueError('invalid fixture image identity')
    except BaseException as error:
        raise RuntimeError('fixture image creation is uncertain; discard runner') from error
    kubeconfig = context / 'kubeconfig'; kubeconfig.write_text('synthetic fixture only'); kubeconfig.chmod(0o644)
    fixture_kubeconfig = str(kubeconfig)
    try:
        network_id = identifier(call('network', 'create', '--driver=bridge',
                                     '--label=faberline.dev/protected-network=' + nonce, name))
    except BaseException as error:
        raise RuntimeError('network creation is uncertain; discard runner') from error
    sidecar = create('sidecar', name); call('start', sidecar)
    test = create('test', name)
    events = load('trusted_event_stream')
    # A process start does not prove Docker subscribed. Retry the setup only,
    # each time with a fresh marker identity, until its connect is observed.
    for attempt in range(3):
        marker = create('marker' + str(attempt), 'bridge'); call('start', marker)
        stream = events.NetworkEventStream(DOCKER, network_id, marker, test)
        call('network', 'connect', network_id, marker)
        try:
            stream.wait_count(1, 10)
        except TimeoutError:
            stream.close(); stream = None
            call('network', 'disconnect', network_id, marker)
            continue
        call('network', 'disconnect', network_id, marker); stream.wait_count(2, 15)
        break
    else:
        raise RuntimeError('Docker event subscription barrier did not establish')
    call('start', test); stream.wait_count(3, 15)
    topology = load('trusted_network')
    network = json.loads(call('network', 'inspect', network_id))[0]
    snapshot = topology.verify_topology(network, inspect_container(sidecar), inspect_container(test),
        name=name, network_id=network_id, nonce=nonce, sidecar_id=sidecar, test_id=test,
        sidecar_image=IMAGE, test_image=IMAGE)
    if snapshot['sidecar']['ip'] == snapshot['test']['ip']: raise ValueError('network namespaces are not isolated')
    call('stop', '--time=5', test); stream.wait_count(4, 15)
    call('network', 'connect', network_id, marker); stream.wait_count(5, 15)
    call('network', 'disconnect', network_id, marker); stream.wait_count(6, 15)
    stream.finish()
    stream.close(); stream = None
    # Exercise the production executor against a synthetic, non-product image.
    # Patching the image regex is confined to this fixture and never affects CI
    # qualification or the production identity rule.
    runner = load('trusted_container'); roster = load('trusted_roster')
    suite = 'e2e::operator::'; test_name = suite + 'journey'
    environment = {'PATH': '/usr/local/bin', 'HOME': '/work', 'LC_ALL': 'C',
                   'KUBECONFIG': '/run/kubeconfig', 'E2E_ENDPOINT': 'http://172.18.0.2:7373'}
    with patch.object(runner, 'IMAGE', r'sha256:[0-9a-f]{64}'):
        exit_code, output = runner.execute(DOCKER, fixture_image, name, fixture_kubeconfig, suite,
            ['/usr/local/bin/lumen-it', '--ignored', '--test-threads=1', suite], environment, 30)
    roster.verify_suite_execution(output, exit_code, [test_name])
    # Demonstrate the protected start barrier using one exact container and
    # event stream. This remains a synthetic fixture, not GKE evidence.
    call('rm', test); resources.remove((test, 'test'))
    start_gate = load('trusted_gate').Gate(context)
    gate = start_gate.path
    try:
        test = identifier(call('create', '--pull=never', '--init', '--read-only', '--user=65532:65532',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--ipc=private', '--cgroupns=private',
            '--network=' + name, '--name=' + name + '-test',
            '--label=faberline.dev/protected-network=' + nonce, '--label=faberline.dev/protected-role=test',
            '--mount=type=bind,source=' + str(gate) + ',target=/run/aw-start/gate,readonly',
            '--mount=type=bind,source=' + str(wrapper) + ',target=/run/aw-start/start,readonly',
            '--mount=type=bind,source=' + str(binary) + ',target=/usr/local/bin/lumen-it,readonly',
            '--env=AW_FIXTURE_SENTINEL=1', '--entrypoint=/run/aw-start/start',
            IMAGE, '--ignored', '--test-threads=1', suite))
        resources.append((test, 'test'))
    except BaseException as error:
        raise RuntimeError('barrier container creation is uncertain; discard runner') from error
    for attempt in range(3):
        marker = create('barrier-marker' + str(attempt), 'bridge'); call('start', marker)
        stream = events.NetworkEventStream(DOCKER, network_id, marker, test)
        call('network', 'connect', network_id, marker)
        try:
            stream.wait_count(1, 10)
        except TimeoutError:
            stream.close(); stream = None
            call('network', 'disconnect', network_id, marker)
            continue
        call('network', 'disconnect', network_id, marker); stream.wait_count(2, 15)
        break
    else:
        raise RuntimeError('start barrier event subscription did not establish')
    started = subprocess.Popen([DOCKER, 'start', '--attach', test], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    stream.wait_count(3, 15)
    network = json.loads(call('network', 'inspect', network_id))[0]
    topology.verify_topology(network, inspect_container(sidecar), inspect_container(test),
        name=name, network_id=network_id, nonce=nonce, sidecar_id=sidecar, test_id=test,
        sidecar_image=IMAGE, test_image=IMAGE)
    if started.poll() is not None or select.select([started.stdout], [], [], 1)[0]:
        raise RuntimeError('suite produced output or stopped before protected release')
    start_gate.release()
    output, _ = started.communicate(timeout=30)
    prefix = b'AW_SYNTHETIC_SUITE_ENTERED\n'
    if not output.startswith(prefix) or output.count(prefix) != 1:
        raise ValueError('synthetic suite entry sentinel is missing or duplicated')
    roster.verify_suite_execution(output[len(prefix):].decode('utf-8'), started.returncode, [test_name])
    started = None
    stream.wait_count(4, 15)
    call('network', 'connect', network_id, marker); stream.wait_count(5, 15)
    call('network', 'disconnect', network_id, marker); stream.wait_count(6, 15)
    stream.finish(); stream.close(); stream = None
finally:
    errors = []
    if started is not None:
        try:
            if started.poll() is None: started.kill()
            started.communicate(timeout=5)
        except Exception as error: errors.append(error)
    if start_gate is not None:
        try: start_gate.close()
        except Exception as error: errors.append(error)
    if stream is not None:
        try: stream.close()
        except Exception as error: errors.append(error)
    for container_id, role in reversed(resources):
        try:
            detail = inspect_container(container_id)
            labels = detail.get('Config', {}).get('Labels') or {}
            if (detail.get('Id') != container_id or detail.get('Name') != '/' + name + '-' + role
                    or labels.get('faberline.dev/protected-network') != nonce
                    or labels.get('faberline.dev/protected-role') != role):
                raise RuntimeError('refuse to remove a foreign container')
            call('rm', '--force', container_id)
        except Exception as error: errors.append(error)
    if network_id is not None:
        try:
            detail = json.loads(call('network', 'inspect', network_id))[0]
            if (detail.get('Id') != network_id or detail.get('Name') != name
                    or (detail.get('Labels') or {}).get('faberline.dev/protected-network') != nonce
                    or detail.get('Containers')):
                raise RuntimeError('refuse to remove a foreign or occupied network')
            call('network', 'rm', network_id)
        except Exception as error: errors.append(error)
    if fixture_image is not None:
        try:
            image = json.loads(call('image', 'inspect', fixture_tag))[0]
            if (image.get('Id') != fixture_image
                    or (image.get('Config', {}).get('Labels') or {}).get('faberline.dev/protected-fixture') != nonce):
                raise RuntimeError('refuse to remove a foreign image')
            call('image', 'rm', fixture_tag)
        except Exception as error: errors.append(error)
    try: temporary.cleanup()
    except Exception as error: errors.append(error)
    if errors: raise RuntimeError('isolated Docker fixture cleanup failed; discard runner') from errors[0]

print('isolated Docker topology and event barriers: PASS')
print('synthetic executor fixture: PASS (not product evidence; event stream not attached)')
print('synthetic start barrier and its own event stream: PASS (not product evidence)')
