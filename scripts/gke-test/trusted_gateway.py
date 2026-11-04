"""Byte gateway for a protected runner sidecar on a dedicated network.

The caller must protect this process and its network namespace, authenticate
the downstream kubectl port-forward, and verify its listening socket remains
owned by this trusted process throughout the test. This is not test evidence.
"""
import asyncio


async def start_gateway(listen_port, upstream_port, *, listen_host):
    if (type(listen_port) is not int or type(upstream_port) is not int
            or not 0 < listen_port < 65536 or not 0 < upstream_port < 65536
            or listen_port == upstream_port or listen_host not in ('127.0.0.1', '0.0.0.0')):
        raise ValueError('distinct valid loopback ports are required')

    async def connection(client_reader, client_writer):
        upstream_writer = None
        tasks = []
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection('127.0.0.1', upstream_port)

            async def copy(source, destination):
                while True:
                    data = await source.read(65536)
                    if not data:
                        if destination.can_write_eof(): destination.write_eof()
                        return
                    destination.write(data)
                    await destination.drain()

            tasks = [asyncio.create_task(copy(client_reader, upstream_writer)),
                     asyncio.create_task(copy(upstream_reader, client_writer))]
            await asyncio.gather(*tasks)
        except (ConnectionError, OSError):
            # The product or port-forward may restart. Closing this connection
            # leaves the test's original retry/timeout policy unchanged.
            pass
        finally:
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            client_writer.close()
            if upstream_writer is not None: upstream_writer.close()
            for writer in (client_writer, upstream_writer):
                if writer is not None:
                    try: await writer.wait_closed()
                    except (ConnectionError, OSError): pass

    return await asyncio.start_server(connection, host=listen_host, port=listen_port)
