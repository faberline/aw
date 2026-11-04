"""Check a protected Docker network event interval.

Events alone are not proof of continuity. A trusted controller must establish
and keep an authenticated live stream, perform the marker operations, bind
start/end snapshots to this interval, detect stream loss, and protect the
Docker daemon. This module does not run Docker or publish acceptance evidence.
"""
import re


def verify_prefix(events, *, network_id, marker_id, test_id):
    """Reject unexpected events before the controller advances a barrier."""
    if (not isinstance(events, list)
            or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in (network_id, marker_id, test_id))
            or len({network_id, marker_id, test_id}) != 3):
        raise ValueError('exact network and marker identities are required')
    expected = [('connect', marker_id), ('disconnect', marker_id), ('connect', test_id),
                ('disconnect', test_id), ('connect', marker_id), ('disconnect', marker_id)]
    if len(events) > len(expected): raise ValueError('network event interval changed')
    previous = 0
    for event, (action, container_id) in zip(events, expected):
        if not isinstance(event, dict): raise ValueError('invalid network event')
        actor = event.get('Actor')
        attributes = actor.get('Attributes') if isinstance(actor, dict) else None
        timestamp = event.get('timeNano')
        if (event.get('Type') != 'network' or event.get('Action') != action
                or not isinstance(actor, dict) or actor.get('ID') != network_id or not isinstance(attributes, dict)
                or attributes.get('container') != container_id
                or type(timestamp) is not int or timestamp <= previous):
            raise ValueError('network event identity, order or timestamp differs')
        previous = timestamp
    return len(events)


def verify_interval(events, *, network_id, marker_id, test_id):
    """Reject any network membership change outside the fixed barrier plan."""
    verify_prefix(events, network_id=network_id, marker_id=marker_id, test_id=test_id)
    if len(events) != 6: raise ValueError('network event interval is incomplete')
    return {'network_id': network_id, 'start_ns': events[1]['timeNano'], 'end_ns': events[4]['timeNano']}
