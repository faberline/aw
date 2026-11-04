"""Verify the cryptographic identity of a protected GKE test image.

This verifier is not a builder or test receipt. It only applies after the
signer workflow has itself been independently reviewed to build from the
protected source without executing PR-controlled inputs. The caller supplies
the identities from protected policy, never from the PR under test.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess


def platform_digest(raw, image, *, os_name, architecture, variant=None):
    """Map a verified image-index digest to one exact platform manifest.

    The caller must authenticate `image` using verify_image, get the bytes of
    that same OCI index, and independently establish the node's platform.
    Single manifests and ambiguous index entries are deliberately unsupported.
    """
    if (not isinstance(raw, bytes) or len(raw) > 4 * 1024 * 1024
            or not isinstance(image, str)
            or not re.fullmatch(r'ghcr\.io/faberline/[a-z0-9._-]+@sha256:[0-9a-f]{64}', image)
            or not all(isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9._-]*', value)
                       for value in (os_name, architecture))
            or not (variant is None or isinstance(variant, str) and re.fullmatch(r'[a-z0-9][a-z0-9._-]*', variant))):
        raise ValueError('immutable index and platform are required')
    if hashlib.sha256(raw).hexdigest() != image.rsplit(':', 1)[1]:
        raise ValueError('index bytes differ from the authenticated digest')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate OCI index key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('invalid OCI index number')
    index = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid, parse_float=invalid)
    if (not isinstance(index, dict) or type(index.get('schemaVersion')) is not int or index['schemaVersion'] != 2
            or index.get('mediaType') not in ('application/vnd.oci.image.index.v1+json',
                                              'application/vnd.docker.distribution.manifest.list.v2+json')
            or not isinstance(index.get('manifests'), list) or not index['manifests']):
        raise ValueError('OCI index is invalid')
    matches = []
    for item in index['manifests']:
        if not isinstance(item, dict) or not isinstance(item.get('platform'), dict):
            raise ValueError('OCI descriptor platform is missing')
        platform = item['platform']
        if (not set(platform) <= {'os', 'architecture', 'variant'}
                or not all(isinstance(platform.get(key), str)
                           and re.fullmatch(r'[a-z0-9][a-z0-9._-]*', platform[key])
                           for key in ('os', 'architecture'))
                or ('variant' in platform and (not isinstance(platform['variant'], str)
                    or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', platform['variant'])))
                or type(item.get('size')) is not int or item['size'] <= 0):
            raise ValueError('unsupported OCI descriptor platform or size')
        if (platform.get('os'), platform.get('architecture'), platform.get('variant')) != (os_name, architecture, variant):
            continue
        digest = item.get('digest')
        if (item.get('mediaType') not in ('application/vnd.oci.image.manifest.v1+json',
                                         'application/vnd.docker.distribution.manifest.v2+json')
                or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)):
            raise ValueError('platform manifest identity is invalid')
        matches.append(digest)
    if len(matches) != 1:
        raise ValueError('exactly one platform manifest is required')
    return matches[0]


def verify_image(gh, image, *, repo, signer_workflow, signer_digest, source_digest,
                 execute=subprocess.run):
    """Require a GitHub-hosted attestation for one immutable OCI image.

    The caller must trust the `gh` binary and GitHub/Sigstore roots, bind the
    signer digest to reviewed source, and independently verify that builder
    does not accept untrusted build instructions. An attestation alone is not
    proof that a test executed or that the claimed build instructions are safe.
    """
    sha = r'[0-9a-f]{40}'
    if (not isinstance(gh, str) or not Path(gh).is_absolute()
            or not isinstance(image, str)
            or not re.fullmatch(r'ghcr\.io/faberline/[a-z0-9._-]+@sha256:[0-9a-f]{64}', image)
            or not isinstance(repo, str) or not re.fullmatch(r'faberline/[a-z0-9._-]+', repo)
            or not isinstance(signer_workflow, str)
            or not re.fullmatch(r'faberline/aw/\.github/workflows/[a-z0-9._-]+\.ya?ml', signer_workflow)
            or not isinstance(signer_digest, str) or not re.fullmatch(sha, signer_digest)
            or not isinstance(source_digest, str) or not re.fullmatch(sha, source_digest)):
        raise ValueError('immutable image, protected workflow, source and signer are required')
    execute([gh, 'attestation', 'verify', 'oci://' + image, '--repo', repo,
             '--signer-workflow', signer_workflow, '--signer-digest', signer_digest,
             '--source-digest', source_digest, '--source-ref', 'refs/heads/main',
             '--deny-self-hosted-runners'], check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=120)
    return {'image': image, 'repo': repo, 'signer_workflow': signer_workflow,
            'signer_digest': signer_digest, 'source_digest': source_digest}
