import asyncio
import importlib.util
from pathlib import Path
import socket
import unittest

spec = importlib.util.spec_from_file_location('trusted_gateway', Path(__file__).resolve().parents[1] / 'trusted_gateway.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxies_bytes_to_loopback_and_preserves_listener(self):
        async def upstream(reader, writer):
            data = await reader.readexactly(4)
            writer.write(data.upper()); await writer.drain()
            writer.close(); await writer.wait_closed()
        server = await asyncio.start_server(upstream, host='127.0.0.1', port=0)
        self.addAsyncCleanup(self.close_server, server)
        upstream_port = server.sockets[0].getsockname()[1]
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0)); listen_port = reserved.getsockname()[1]
        gateway = await module.start_gateway(listen_port, upstream_port, listen_host='127.0.0.1')
        self.addAsyncCleanup(self.close_server, gateway)
        for _ in range(2):
            reader, writer = await asyncio.open_connection('127.0.0.1', listen_port)
            writer.write(b'test'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 2), b'TEST')
            writer.close(); await writer.wait_closed()

    async def test_invalid_ports_fail_closed(self):
        for listen, upstream in ((0, 100), (100, 100), (True, 100), (100, 65536)):
            with self.subTest(listen=listen, upstream=upstream), self.assertRaises(ValueError):
                await module.start_gateway(listen, upstream, listen_host='127.0.0.1')
        with self.assertRaises(ValueError):
            await module.start_gateway(100, 200, listen_host='192.0.2.1')

    @staticmethod
    async def close_server(server):
        server.close(); await server.wait_closed()


if __name__ == '__main__':
    unittest.main()
