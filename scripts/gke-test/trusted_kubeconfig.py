"""Validate a minimal in-memory kubeconfig for an isolated test client.

The caller must obtain the token from the authenticated Kubernetes API for
the reviewed namespace ServiceAccount, prove its permissions and expiry, and
protect the credential file. Parsing JWT claims alone does not prove identity.
This function does not print or return the token.
"""
import base64
import hashlib
import json
import re
from urllib.parse import urlsplit


def verify(raw, *, server, ca_sha256, namespace):
    if (not isinstance(raw, str) or len(raw.encode('utf-8')) > 131072
            or not isinstance(server, str) or not isinstance(ca_sha256, str)
            or not re.fullmatch(r'[0-9a-f]{64}', ca_sha256)
            or not isinstance(namespace, str) or len(namespace) > 63
            or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?', namespace)):
        raise ValueError('trusted cluster and namespace identity is required')
    try:
        endpoint = urlsplit(server)
        if (any(ord(character) <= 32 or ord(character) == 127 for character in server)
                or endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username is not None
                or endpoint.password is not None or endpoint.path not in ('', '/')
                or endpoint.query or endpoint.fragment):
            raise ValueError('invalid trusted API endpoint')
        if endpoint.port == 0: raise ValueError('invalid trusted API endpoint port')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('duplicate kubeconfig key')
                result[key] = value
            return result
        def invalid(_value): raise ValueError('invalid kubeconfig number')
        config = json.loads(raw, object_pairs_hook=unique, parse_float=invalid, parse_constant=invalid)
        if (not isinstance(config, dict)
                or set(config) != {'apiVersion', 'kind', 'preferences', 'clusters', 'contexts', 'current-context', 'users'}
                or config['apiVersion'] != 'v1' or config['kind'] != 'Config' or config['preferences'] != {}
                or config['current-context'] != 'protected'
                or not all(isinstance(config[key], list) and len(config[key]) == 1 for key in ('clusters', 'contexts', 'users'))):
            raise ValueError('invalid kubeconfig structure')
        cluster, context, user = (config[key][0] for key in ('clusters', 'contexts', 'users'))
        if (not isinstance(cluster, dict) or set(cluster) != {'name', 'cluster'} or cluster['name'] != 'protected'
                or not isinstance(cluster['cluster'], dict)
                or set(cluster['cluster']) != {'server', 'certificate-authority-data'}
                or cluster['cluster']['server'] != server
                or not isinstance(context, dict) or set(context) != {'name', 'context'} or context['name'] != 'protected'
                or context['context'] != {'cluster': 'protected', 'user': 'protected', 'namespace': namespace}
                or not isinstance(user, dict) or set(user) != {'name', 'user'} or user['name'] != 'protected'
                or not isinstance(user['user'], dict) or set(user['user']) != {'token'}):
            raise ValueError('kubeconfig trust boundary differs')
        token = user['user']['token']
        if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token)):
            raise ValueError('service account token is invalid')
        certificate = base64.b64decode(cluster['cluster']['certificate-authority-data'], validate=True)
        if not certificate or hashlib.sha256(certificate).hexdigest() != ca_sha256:
            raise ValueError('cluster CA differs')
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid protected kubeconfig') from exc
    return {'server': server, 'ca_sha256': ca_sha256, 'namespace': namespace}
