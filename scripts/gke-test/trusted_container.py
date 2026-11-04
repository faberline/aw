"""Isolated Docker execution for a protected GH-hosted test runner.

The caller must authenticate the image and sidecar, protect this source and
Docker daemon, provision a least-privilege kubeconfig, and bind all inputs to
reviewed policy. This module does not publish evidence or establish those
preconditions. It deliberately never uses host networking or a Docker socket.
"""
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import time


MAX_OUTPUT = 1024 * 1024
LABEL = 'faberline.dev/protected-test'
IMAGE = r'ghcr\.io/faberline/[a-z0-9._-]+@sha256:[0-9a-f]{64}'
ENV = {'PATH', 'HOME', 'KUBECONFIG', 'LC_ALL', 'E2E_ENDPOINT', 'E2E_NAMESPACE', 'E2E_POD_NAME',
       'E2E_DOC_COUNT', 'E2E_EXPECTED_GIT_SHA', 'E2E_EXPECTED_IMAGE',
       'E2E_EXPECTED_RUNTIME_DIGEST', 'E2E_EXPECTED_VERSION'}


class CleanupUncertain(RuntimeError):
    """The Docker daemon may still create a container after client timeout.

    The entire ephemeral GH-hosted runner must be discarded. An empty current
    inventory is not evidence that the in-flight create has finished.
    """


def verify_image_config(config):
    """Reject implicit writable mounts, environment and image healthchecks."""
    if not isinstance(config, dict) or config.get('Volumes') not in (None, {}):
        raise ValueError('image declares implicit volumes')
    env = config.get('Env')
    if env is None: env = []
    if not isinstance(env, list): raise ValueError('image environment is invalid')
    keys = []
    for row in env:
        if not isinstance(row, str) or '=' not in row:
            raise ValueError('image environment is invalid')
        key, _ = row.split('=', 1)
        keys.append(key)
    # PATH is replaced explicitly for every process. All other inherited
    # environment is refused, including dynamic loaders and proxy settings.
    if len(keys) != len(set(keys)) or any(key != 'PATH' for key in keys):
        raise ValueError('image declares unreviewed environment')


def command(docker, image, network, kubeconfig, suite, argv, environment, nonce):
    """Build only one of the reviewed list or original whole-suite commands."""
    if (not isinstance(docker, str) or not Path(docker).is_absolute()
            or not isinstance(image, str) or not re.fullmatch(IMAGE, image)
            or not isinstance(network, str) or not re.fullmatch(r'aw-protected-[0-9a-f]{32}', network)
            or not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or not isinstance(suite, str) or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,256}', suite)
            or not isinstance(environment, dict) or not set(environment) <= ENV
            or any(not isinstance(v, str) or '\0' in v or '\n' in v for v in environment.values())):
        raise ValueError('invalid protected container identity')
    binary = '/usr/local/bin/lumen-it'
    lists = ([binary, suite, '--list'], [binary, suite, '--ignored', '--list'])
    case = [binary, '--ignored', '--test-threads=1', suite]
    if argv not in (*lists, case): raise ValueError('unreviewed test command')
    listing = argv in lists
    if (environment.get('PATH') != '/usr/local/bin' or environment.get('HOME') != '/work'
            or environment.get('LC_ALL') != 'C'
            or (listing and set(environment) != {'PATH', 'HOME', 'LC_ALL'})
            or (not listing and environment.get('KUBECONFIG') != '/run/kubeconfig')):
        raise ValueError('unreviewed test environment')
    args = [docker, 'create', '--pull=never', '--init', '--read-only', '--no-healthcheck',
            '--user=65532:65532', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--ipc=private', '--cgroupns=private',
            '--tmpfs=/work:rw,nosuid,nodev,noexec,size=1g', '--name=aw-protected-' + nonce,
            '--label=' + LABEL + '=' + nonce,
            '--label=faberline.dev/protected-network=' + network.removeprefix('aw-protected-'),
            '--label=faberline.dev/protected-role=test',
            '--network=' + ('none' if listing else network),
            '--workdir=/work', '--entrypoint=' + binary]
    if not listing:
        if (not isinstance(kubeconfig, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', kubeconfig)
                or '..' in Path(kubeconfig).parts):
            raise ValueError('safe protected kubeconfig path required')
        info = os.lstat(kubeconfig)
        if not stat.S_ISREG(info.st_mode): raise ValueError('kubeconfig must be a regular non-symlink file')
        args += ['--mount=type=bind,source=' + kubeconfig + ',target=/run/kubeconfig,readonly']
    for key in sorted(environment): args += ['--env', key + '=' + environment[key]]
    return args + [image] + argv[1:]


def bounded(command_line, timeout):
    """Capture bounded container output without streaming it to Actions logs."""
    process = subprocess.Popen(command_line, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining): raise TimeoutError('container timed out')
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    selector.unregister(process.stdout)
                    break
                output.extend(block)
                if len(output) > MAX_OUTPUT: raise ValueError('container output exceeds limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError('container timed out')
        return process.wait(timeout=remaining), output.decode('utf-8')
    except BaseException:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        raise
    finally:
        process.stdout.close()


def cleanup(docker, nonce, *, execute=subprocess.run):
    """Remove only this invocation's containers and prove none remain."""
    if not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce):
        raise ValueError('invalid cleanup identity')
    def inventory():
        row = execute([docker, 'container', 'ls', '--all', '--quiet', '--no-trunc',
                       '--filter=label=' + LABEL + '=' + nonce], check=True, capture_output=True, text=True, timeout=30)
        ids = row.stdout.splitlines()
        if len(ids) > 1 or len(ids) != len(set(ids)) or any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in ids):
            raise ValueError('invalid container cleanup inventory')
        return ids
    for container_id in inventory():
        result = execute([docker, 'inspect', '--format', '{{json .}}', container_id],
                         check=True, capture_output=True, text=True, timeout=30)
        details = json.loads(result.stdout)
        if (not isinstance(details, dict) or details.get('Id') != container_id
                or details.get('Name') != '/aw-protected-' + nonce
                or (details.get('Config') or {}).get('Labels', {}).get(LABEL) != nonce):
            raise ValueError('container cleanup identity differs')
        execute([docker, 'rm', '--force', container_id], check=True, capture_output=True, text=True, timeout=30)
    if inventory(): raise ValueError('protected test container cleanup incomplete')


def require_cleanup(docker, nonce):
    """A failed cleanup always poisons the entire ephemeral GH runner."""
    try:
        cleanup(docker, nonce)
    except BaseException as exc:
        raise CleanupUncertain('container cleanup is unverified; discard this ephemeral runner: ' + nonce) from exc


def verify_created(details, *, container_id, image, nonce, network, argv, environment, kubeconfig):
    """Check the daemon's created container before starting any test code."""
    if not isinstance(details, dict): raise ValueError('created container is invalid')
    config = details.get('Config') or {}
    host = details.get('HostConfig') or {}
    state = details.get('State') or {}
    if not all(isinstance(value, dict) for value in (config, host, state)):
        raise ValueError('created container metadata is invalid')
    expected_network = 'none' if '--list' in argv else network
    options = host.get('SecurityOpt')
    env_rows = config.get('Env')
    mounts = details.get('Mounts')
    if (details.get('Id') != container_id or details.get('Name') != '/aw-protected-' + nonce
            or state.get('Status') != 'created' or config.get('Image') != image
            or (config.get('Labels') or {}).get(LABEL) != nonce
            or config.get('User') != '65532:65532' or config.get('Entrypoint') != [argv[0]]
            or config.get('Cmd') != argv[1:] or config.get('WorkingDir') != '/work'
            or host.get('NetworkMode') != expected_network or host.get('ReadonlyRootfs') is not True
            or host.get('Privileged') is not False or host.get('CapDrop') != ['ALL']
            or 'CapAdd' not in host or host['CapAdd'] not in (None, []) or host.get('Init') is not True
            or host.get('RestartPolicy', {}).get('Name') != 'no'
            or type(details.get('RestartCount')) is not int or details['RestartCount'] != 0
            or host.get('PidMode') != '' or host.get('IpcMode') != 'private'
            or host.get('UTSMode') != '' or host.get('CgroupnsMode') != 'private'
            or not isinstance(options, list) or not any(x in ('no-new-privileges', 'no-new-privileges=true') for x in options)
            or any(not isinstance(x, str) or x in ('seccomp=unconfined', 'apparmor=unconfined', 'label=disable') for x in options)
            or not isinstance(env_rows, list) or any(not isinstance(row, str) or '=' not in row for row in env_rows)
            or not isinstance(mounts, list)):
        raise ValueError('created protected container differs from the fixed command')
    env = [row.split('=', 1) for row in env_rows]
    if len({row[0] for row in env}) != len(env) or dict(env) != environment:
        raise ValueError('created container environment differs')
    if not isinstance(host.get('Tmpfs'), dict) or set(host['Tmpfs']) != {'/work'}:
        raise ValueError('protected scratch mount differs')
    scratch = host['Tmpfs']['/work']
    if (not isinstance(scratch, str) or len(scratch.split(',')) != 5
            or set(scratch.split(',')) != {'rw', 'nosuid', 'nodev', 'noexec', 'size=1g'}):
        raise ValueError('protected scratch mount options differ')
    binds = []
    for mount in mounts:
        if not isinstance(mount, dict): raise ValueError('invalid container mount')
        if mount.get('Type') == 'tmpfs' and mount.get('Destination') == '/work':
            continue
        binds.append(mount)
    if '--list' in argv:
        if binds: raise ValueError('list container has unexpected mounts')
    elif (len(binds) != 1 or binds[0].get('Type') != 'bind' or binds[0].get('Source') != kubeconfig
          or binds[0].get('Destination') != '/run/kubeconfig' or binds[0].get('RW') is not False):
        raise ValueError('test credential mount differs')


def execute(docker, image, network, kubeconfig, suite, argv, environment, timeout):
    """Run a single isolated process and always clean its own container."""
    if type(timeout) is not int or not 0 < timeout <= 5400:
        raise ValueError('invalid protected test timeout')
    nonce = secrets.token_hex(16)
    args = command(docker, image, network, kubeconfig, suite, argv, environment, nonce)
    inspect = subprocess.run([docker, 'image', 'inspect', '--format', '{{json .Config}}', image],
                             check=True, capture_output=True, text=True, timeout=30)
    verify_image_config(json.loads(inspect.stdout))
    try:
        try:
            created = subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)
            container_id = created.stdout.strip()
            if not re.fullmatch(r'[0-9a-f]{64}', container_id):
                raise ValueError('container creation returned an invalid identity')
        except BaseException as exc:
            # The daemon may create the container after an empty cleanup scan.
            # Never continue or issue evidence on this GH-hosted runner.
            raise CleanupUncertain('container creation outcome is unknown; discard this ephemeral runner: ' + nonce) from exc
        item = subprocess.run([docker, 'inspect', '--format', '{{json .}}', container_id],
                              check=True, capture_output=True, text=True, timeout=30)
        details = json.loads(item.stdout)
        verify_created(details, container_id=container_id, image=image, nonce=nonce, network=network,
                       argv=argv, environment=environment, kubeconfig=kubeconfig)
        start_code, output = bounded([docker, 'start', '--attach', container_id], timeout)
        terminal = subprocess.run([docker, 'inspect', '--format', '{{json .}}', container_id],
                                  check=True, capture_output=True, text=True, timeout=30)
        final = json.loads(terminal.stdout)
        state = final.get('State') if isinstance(final, dict) else None
        if (not isinstance(final, dict) or final.get('Id') != container_id
                or type(final.get('RestartCount')) is not int or final['RestartCount'] != 0
                or not isinstance(state, dict) or state.get('Status') != 'exited'
                or type(state.get('ExitCode')) is not int or state.get('OOMKilled') is not False
                or state.get('Dead') is not False or state.get('Error') != ''
                or start_code not in (0, state['ExitCode'])):
            raise ValueError('protected container did not reach a verified terminal state')
        return state['ExitCode'], output
    except (KeyboardInterrupt, SystemExit) as exc:
        raise CleanupUncertain('container execution was interrupted; discard this ephemeral runner: ' + nonce) from exc
    finally:
        require_cleanup(docker, nonce)
