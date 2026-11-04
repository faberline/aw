"""Bind one trusted Docker snapshot to a retained Linux process identity.

The caller must authenticate the Docker daemon and snapshot, preserve its
container and network isolation, and continuously recheck until teardown.
This primitive does not produce test evidence or eliminate races.
"""
import importlib.util
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location('aw_running_process', Path(__file__).with_name('trusted_process_identity.py'))
process_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(process_module)


def verify(snapshot, process, *, container_id, nonce, network, image):
    if (not isinstance(process, process_module.ProcessIdentity)
            or not isinstance(container_id, str) or not re.fullmatch(r'[0-9a-f]{64}', container_id)
            or not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or not isinstance(network, str) or not re.fullmatch(r'aw-protected-[0-9a-f]{32}', network)
            or not isinstance(image, str) or not re.fullmatch(r'ghcr\.io/faberline/[a-z0-9._-]+@sha256:[0-9a-f]{64}', image)
            or not isinstance(snapshot, dict)):
        raise ValueError('invalid running container identity')
    process.check()
    config, state, host = (snapshot.get(key) for key in ('Config', 'State', 'HostConfig'))
    if (not all(isinstance(value, dict) for value in (config, state, host))
            or snapshot.get('Id') != container_id or snapshot.get('Name') != '/aw-protected-' + nonce
            or type(snapshot.get('RestartCount')) is not int or snapshot['RestartCount'] != 0
            or state.get('Status') != 'running' or state.get('Running') is not True
            or type(state.get('Pid')) is not int or state['Pid'] != process.pid
            or state.get('Dead') is not False or state.get('OOMKilled') is not False or state.get('Error') != ''
            or config.get('Image') != image or config.get('Entrypoint') != ['/run/aw-start/start']
            or (config.get('Labels') or {}).get('faberline.dev/protected-test') != nonce
            or (config.get('Labels') or {}).get('faberline.dev/protected-network') != network.removeprefix('aw-protected-')
            or (config.get('Labels') or {}).get('faberline.dev/protected-role') != 'test'
            or host.get('NetworkMode') != network):
        raise ValueError('running container identity differs')
    process.check()
