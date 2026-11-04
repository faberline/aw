"""Validate one observed protected Docker network topology.

The caller must authenticate Docker API data, policy, image provenance,
sidecar process and file mounts, and continuously monitor the entire test.
A single snapshot is not a test result or a continuous isolation guarantee.
"""
import ipaddress
import re

PRIVATE = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def verify_topology(network, sidecar, test, *, name, network_id, nonce, sidecar_id, test_id,
                    sidecar_image, test_image):
    def require(condition, message):
        if not condition: raise ValueError(message)
    require(isinstance(nonce, str) and re.fullmatch(r'[0-9a-f]{32}', nonce)
            and name == 'aw-protected-' + nonce
            and all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                    for value in (network_id, sidecar_id, test_id)) and sidecar_id != test_id
            and all(isinstance(value, str) and re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', value)
                    for value in (sidecar_image, test_image)),
            'network and container identity is incomplete')
    require(all(isinstance(value, dict) for value in (network, sidecar, test)), 'Docker snapshot is invalid')
    labels = network.get('Labels')
    require(network.get('Id') == network_id and network.get('Name') == name
            and network.get('Driver') == 'bridge' and network.get('Scope') == 'local'
            and network.get('Internal') is False and network.get('Ingress') is False
            and network.get('Attachable') is False and network.get('EnableIPv6') is False
            and isinstance(labels, dict) and labels.get('faberline.dev/protected-network') == nonce
            and network.get('Options') in (None, {}), 'network is not the protected bridge')
    ipam = network.get('IPAM')
    require(isinstance(ipam, dict) and ipam.get('Driver') == 'default'
            and isinstance(ipam.get('Config'), list) and len(ipam['Config']) == 1
            and isinstance(ipam['Config'][0], dict), 'network address configuration is ambiguous')
    try:
        subnet = ipaddress.ip_network(ipam['Config'][0]['Subnet'], strict=True)
        gateway = ipaddress.ip_address(ipam['Config'][0]['Gateway'])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('network address configuration is invalid') from exc
    require(subnet.version == 4 and subnet.prefixlen >= 16 and any(subnet.subnet_of(p) for p in PRIVATE)
            and gateway in subnet and gateway not in (subnet.network_address, subnet.broadcast_address),
            'network must use a bounded private IPv4 subnet')
    members = network.get('Containers')
    require(isinstance(members, dict) and set(members) == {sidecar_id, test_id},
            'dedicated network has unexpected or missing members')
    observed = {}
    for role, container_id, container, image in (('sidecar', sidecar_id, sidecar, sidecar_image),
                                                  ('test', test_id, test, test_image)):
        config, host, state, settings = (container.get(key) for key in ('Config', 'HostConfig', 'State', 'NetworkSettings'))
        require(all(isinstance(value, dict) for value in (config, host, state, settings))
                and container.get('Id') == container_id and config.get('Image') == image
                and (config.get('Labels') or {}).get('faberline.dev/protected-network') == nonce
                and (config.get('Labels') or {}).get('faberline.dev/protected-role') == role
                and state.get('Running') is True and state.get('Status') == 'running'
                and type(container.get('RestartCount')) is int and container['RestartCount'] == 0
                and host.get('NetworkMode') == name and host.get('Privileged') is False
                and host.get('ReadonlyRootfs') is True and host.get('CapDrop') == ['ALL']
                and 'CapAdd' in host and host['CapAdd'] in (None, []) and host.get('Init') is True
                and host.get('RestartPolicy', {}).get('Name') == 'no'
                and 'PortBindings' in host and host['PortBindings'] in (None, {}) and config.get('User') == '65532:65532'
                and isinstance(host.get('SecurityOpt'), list)
                and all(isinstance(x, str) for x in host['SecurityOpt'])
                and any(x in ('no-new-privileges', 'no-new-privileges=true') for x in host['SecurityOpt'])
                and not any(x in ('seccomp=unconfined', 'apparmor=unconfined', 'label=disable') for x in host['SecurityOpt'])
                and host.get('PidMode') == '' and host.get('IpcMode') == 'private'
                and host.get('UTSMode') == '' and host.get('CgroupnsMode') == 'private'
                and isinstance(settings.get('SandboxKey'), str) and settings['SandboxKey']
                and isinstance(settings.get('Networks'), dict) and set(settings['Networks']) == {name},
                'container isolation or identity differs')
        require('Ports' in settings, 'host port inventory is missing')
        ports = settings['Ports']
        require(ports is None or isinstance(ports, dict) and all(value in (None, []) for value in ports.values()),
                'host port publication is not allowed')
        endpoint = settings['Networks'][name]
        member = members[container_id]
        require(isinstance(endpoint, dict) and endpoint.get('NetworkID') == network_id and isinstance(member, dict),
                'container network identity differs')
        try:
            address = ipaddress.ip_address(endpoint['IPAddress'])
            member_address = ipaddress.ip_interface(member['IPv4Address'])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError('container address is invalid') from exc
        require(address.version == 4 and address in subnet and address not in (gateway, subnet.network_address, subnet.broadcast_address)
                and member_address.ip == address and member_address.network == subnet,
                'container address differs from protected bridge')
        observed[role] = {'id': container_id, 'ip': str(address), 'sandbox': settings['SandboxKey']}
    require(observed['sidecar']['ip'] != observed['test']['ip']
            and observed['sidecar']['sandbox'] != observed['test']['sandbox'],
            'test and sidecar must have separate network namespaces')
    return observed
