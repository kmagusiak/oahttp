import asyncio
from collections.abc import Callable

from . import config
from .base_protocol import BaseBufferedProtocol


class LoadBalanceProtocol(BaseBufferedProtocol):
    protocol_name = "lb"

    def __init__(self, protocol_factory: Callable[[], asyncio.BaseProtocol]):
        super().__init__(buffer_size=config.BUFFER_SIZE)
        self.protocol_factory = protocol_factory
        # TODO handle timeout

    def eof_received(self):
        self._transport.abort()

    def buffer_updated(self, nbytes):
        super().buffer_updated(nbytes)
        self.balance()

    def balance(self):
        """
        Balance the connection.

        By default, run it locally in the provided protocol.
        You can overwrite it to:
        - Do nothing: wait for more data.
        - Send file descriptor elsewhere and abort.
        - Abort the connection.
        """

        # local execution
        protocol = self.protocol_factory()
        self._transport.set_protocol(protocol)
        protocol.connection_made(self._transport)
        data = memoryview(self._read_buffer)
        if isinstance(protocol, asyncio.BufferedProtocol):
            while nbytes := len(data):
                buf = protocol.get_buffer(nbytes)
                buf_size = len(buf)
                if buf_size >= nbytes:
                    buf[:nbytes] = data
                    protocol.buffer_updated(nbytes)
                    break
                else:
                    buf[:] = data[:buf_size]
                    data = data[buf_size:]
                    protocol.buffer_updated(buf_size)
        elif isinstance(protocol, asyncio.Protocol):
            protocol.data_received(data)
        else:
            self._transport.abort()
            raise RuntimeError(f"invalid protocol for load balancing: {protocol}")
