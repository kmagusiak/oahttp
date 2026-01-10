# Specifications:
# https://datatracker.ietf.org/doc/html/rfc9110 - HTTP semantics
# https://datatracker.ietf.org/doc/html/rfc9111 - HTTP caching
# https://datatracker.ietf.org/doc/html/rfc9112 - HTTP 1.1
# https://datatracker.ietf.org/doc/html/rfc6265 - Cookies
# https://datatracker.ietf.org/doc/html/rfc6455 - websocket
# https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Resources_and_specifications

from __future__ import annotations

import asyncio
import socket
import typing

from . import _logger, config
from .base_protocol import ReadBuffer
from .request import Request

if typing.TYPE_CHECKING:
    from .response import Response
    from .router import HttpStrategy


class HttpConnection(asyncio.BufferedProtocol):
    request: Request | None = None
    transport: asyncio.Transport | None = None
    __process_task: asyncio.Task[None] | None = None

    def __init__(self, strategy: HttpStrategy):
        self.strategy = strategy
        self.read_buffer = ReadBuffer(config.BUFFER_SIZE)
        self.keep_alive: bool = True
        self.__write_allowed = asyncio.Event()
        self.__write_allowed.set()
        # optimization, bind the call
        self.get_buffer = self.read_buffer.write_buffer

    def pause_writing(self):
        self.__write_allowed.clear()

    def resume_writing(self):
        self.__write_allowed.set()

    def connection_made(self, transport):
        _logger.debug("%s: connection_made %s", self, transport)
        self.transport = transport  # type: ignore

    def connection_lost(self, exc):
        _logger.debug("%s: connection_lost, %s", self, exc or 'closed')
        if task := self.__process_task:
            task.cancel("connection lost")

        self.request = None
        self.transport = None

    def eof_received(self):
        _logger.debug("%s: eof_received", self)
        if self.request is None:
            # no request, just close normally
            self.transport.close()
        elif self.request.ready and self.request.body.ready:
            # all received, just let it finish
            self.keep_alive = False
        else:
            self.abort()

    @typing.final
    def get_buffer(self, sizehint):
        return self.read_buffer.write_buffer(sizehint)

    def buffer_updated(self, nbytes):
        buffer = self.read_buffer
        if not nbytes and buffer.empty:
            # nothing received, called internally
            return
        _logger.debug("%s: buffer_updated", self)
        buffer.written(nbytes)

        request = self.request
        if request is None:
            self.request = request = Request(self.strategy)
            ready = False
        else:
            ready = request.ready
        try:
            request._receive_data(buffer)
        except BufferError:
            if not request.method:
                from .response import GenericResponse

                too_long = b'414 URI too long'
                self.abort(GenericResponse(too_long, status=too_long))
                return
            self.transport.close()
            return
        except Exception:
            _logger.exception("receive")
        if buffer.full:
            if not request.ready:
                # buffer full, header not parsed
                self.abort()
                return
            self.transport.pause_reading()
            request.body.receive_paused(self.transport.resume_reading)

        if ready or not request.ready:
            return

        _logger.debug("%s: route request %r %r", self, request.target, request.http_version)
        if self.keep_alive:
            self.keep_alive = request.http_version == b'1.1' and (
                not (connection := request.headers.get(b'connection'))
                or connection.lower() == b'keep-alive'
            )
        expect_body = not request.body.ready
        context = request.context
        try:
            dispatch = context.run(self.strategy.route, request)
        except Exception as e:
            from .response import Response

            if isinstance(e, Response):
                expect_body = False
                exc = e
                dispatch = lambda: exc
            else:
                self.abort(self.strategy.wrap_error(self.request, e))
                return
        if True:
            if expect := request.headers.get(b'expect') and request.http_version == b'1.1':
                from .response import ContinueResponse, ExpecationFailed

                if expect != b'100-continue':
                    self.abort(ExpecationFailed(f"got: {expect}"))
                    return
                if expect_body:
                    ContinueResponse().send_immediately(request, self.transport)

        try:
            response = context.run(dispatch)
        except Exception as ex:
            response = self.strategy.wrap_error(ex)

        _logger.debug("%s: response ready", self)
        request = self.request
        transport = self.transport
        response.send_sync(request, transport)

        if self.request.headers.get(b'connection') == b'upgrade':
            from .response import UpgradeRequired, UpgradeResponse

            if isinstance(response, UpgradeResponse):
                assert self.keep_alive, "must be kept alive"
                new_protocol = response.new_protocol
                _logger.debug("%s: upgrading to %s", self, new_protocol)
                transport.set_protocol(new_protocol)
                new_protocol.connection_made(transport)
                data = memoryview(self.read_buffer)
                size = len(data)
                buf = new_protocol.get_buffer(size)
                assert len(buf) >= size, "Could not allocate a big buffer in the new protocol"
                buf[:size] = data
                new_protocol.buffer_updated(size)
                _logger.info("%s: upgraded to %s", self, new_protocol)
                return
            if not isinstance(response, UpgradeRequired):
                _logger.warning("%s: invalid response to connection upgrade header", self)
                self.keep_alive = False

        self._prepare_next()

    def _prepare_next(self):
        if self.keep_alive:
            _logger.debug("%s: prepare next", self)
            self.request = None
            self.transport.resume_reading()
            # parse what is already in the buffer
            self.buffer_updated(0)
        else:
            # close cleanly
            self.transport.close()

    def abort(self, response: Response | None = None):
        _logger.debug("%s: abort %s", self, response)
        if task := self.__process_task:
            task.cancel("aborted")
        if response is not None:  # TODO and not already started sending
            response.send_immediately(self.request, self.transport)
            self.transport.close()
        else:
            self.transport.abort()

    def __repr__(self):
        return f"HTTP{id(self)}"


class SyncTransport(asyncio.Transport):
    def __init__(self, socket: socket.socket, extra=None):
        self.socket = socket
        self.protocol = None
        self.state: typing.Literal['init', 'running', 'closing', 'closed'] = 'init'
        super().__init__(extra)

    def is_closing(self):
        return self.state == 'closing'

    def close(self):
        if self.state in ('closing', 'closed'):
            return
        self.state = 'closing'
        self.socket.shutdown(socket.SHUT_RDWR)

    def abort(self):
        if self.state == 'closed':
            return
        self.socket.close()
        self.state = 'closed'

    def get_protocol(self):
        return self.protocol

    def set_protocol(self, protocol):
        self.protocol = protocol

    def is_reading(self):
        return False

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass

    def write(self, data):
        self.socket.sendall(data)

    def can_write_eof(self):
        return self.state == 'running'

    def write_eof(self):
        self.socket.shutdown(socket.SHUT_WR)

    def run(self):
        sock = self.socket
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        assert self.state == 'init'
        self.state == 'running'
        self.protocol.connection_made(self)

        try:
            while True:
                protocol: asyncio.BufferedProtocol = self.protocol  # TODO ignore type
                buffer = protocol.get_buffer()
                nbytes = sock.recv_into(buffer)
                if not nbytes:
                    if not protocol.eof_received():
                        self.close()
                    break
                protocol.buffer_updated(nbytes)
        except Exception as e:
            self.protocol.connection_lost(e)
        else:
            self.protocol.connection_lost()
        finally:
            self.state = 'closed'
            self.socket.close()
