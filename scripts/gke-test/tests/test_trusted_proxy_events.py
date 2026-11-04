import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('proxy_reports', Path(__file__).resolve().parents[1] / 'trusted_proxy_events.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def line(kind, **values):
    return (kind + ' ' + json.dumps(values) + '\n').encode()


class ProxyReportsTests(unittest.TestCase):
    def setUp(self): self.reports = module.ProxyReports(7373)

    def gateway(self):
        self.reports.ingest(line('AW_GKE_GATEWAY_V1', state='ready', pid=7, address='0.0.0.0', port=7373))

    def test_allows_upstream_restart_and_requires_current_readiness(self):
        self.gateway()
        with self.assertRaises(ValueError): self.reports.require_ready()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8))
        self.reports.require_ready()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='lost', generation=1, pid=8))
        with self.assertRaises(ValueError): self.reports.require_ready()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8))
        self.reports.require_ready()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='lost', generation=1, pid=8))
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=3, pid=9))
        self.reports.require_ready()

    def test_same_generation_cannot_change_process(self):
        self.gateway()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8))
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='lost', generation=1, pid=8))
        with self.assertRaises(ValueError):
            self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=9))

    def test_barrier_is_bound_to_gateway_pid_and_never_replayed(self):
        self.gateway()
        challenge = 'a' * 32
        report = line('AW_GKE_BARRIER_V1', pid=7, challenge=challenge)
        self.reports.ingest(report)
        self.assertEqual(self.reports.latest_barrier, challenge)
        with self.assertRaises(ValueError): self.reports.ingest(report)
        self.setUp(); self.gateway()
        with self.assertRaises(ValueError):
            self.reports.ingest(line('AW_GKE_BARRIER_V1', pid=8, challenge=challenge))

    def test_malformed_forged_or_out_of_order_reports_fail(self):
        invalid = [b'', b'other {}\n', b'AW_GKE_GATEWAY_V1 {"pid":1,"pid":2}\n',
                   line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8),
                   line('AW_GKE_GATEWAY_V1', state='ready', pid=True, address='0.0.0.0', port=7373)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError): module.ProxyReports(7373).ingest(value)
        for value in (line('AW_GKE_PROXY_V1', state='lost', generation=1, pid=8),
                      line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=True)):
            self.setUp(); self.gateway()
            with self.subTest(value=value), self.assertRaises(ValueError): self.reports.ingest(value)
            with self.assertRaises(ValueError): self.reports.require_ready()
        self.setUp(); self.gateway()
        self.reports.ingest(line('AW_GKE_PROXY_V1', state='ready', generation=1, pid=8))
        with self.assertRaises(ValueError):
            self.reports.ingest(line('AW_GKE_PROXY_V1', state='lost', generation=1, pid=9))
        self.setUp(); self.gateway()
        with self.assertRaises(ValueError): self.gateway()
