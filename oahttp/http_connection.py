# Specifications:
# https://datatracker.ietf.org/doc/html/rfc9110 - HTTP semantics
# https://datatracker.ietf.org/doc/html/rfc9111 - HTTP caching
# https://datatracker.ietf.org/doc/html/rfc9112 - HTTP 1.1
# https://datatracker.ietf.org/doc/html/rfc6265 - Cookies
# https://datatracker.ietf.org/doc/html/rfc6455 - websocket
# https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Resources_and_specifications

from __future__ import annotations

import asyncio
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
        loop = asyncio.get_running_loop()
        eager = asyncio.eager_task_factory
        expect_body = not request.body.ready
        context = request.context
        try:
            dispatch: asyncio.Future[Response] = eager(
                loop, self.strategy.dispatcher(request), context=context
            )
        except Exception as e:
            from .response import Response

            if isinstance(e, Response):
                expect_body = False
                dispatch = asyncio.Future()
                dispatch.set_result(e)
            else:
                self.abort(self.strategy.wrap_error(self.request, e))
                return
        try:
            if expect := request.headers.get(b'expect') and request.http_version == b'1.1':
                from .response import ContinueResponse, ExpecationFailed

                if expect != b'100-continue':
                    self.abort(ExpecationFailed(f"got: {expect}"))
                    return
                if expect_body:
                    ContinueResponse().send_immediately(request, self.transport)

            self.__process_task = eager(loop, self._response_callback(dispatch), context=context)
            self.__process_task.add_done_callback(self._prepare_next)
        except BaseException:
            dispatch.cancel()
            raise

    async def _response_callback(self, task: asyncio.Future[Response]):
        # wait until previous request is flushed
        write_throttle = self.__write_allowed.wait
        await write_throttle()

        try:
            if task.done():
                response = task.result()
            else:
                async with asyncio.timeout(config.TIMEOUT_PROCESS):
                    response = await task
        except Exception as ex:
            response = self.strategy.wrap_error(ex)

        _logger.debug("%s: response ready", self)
        request = self.request
        transport = self.transport
        await response.send(request, transport, write_throttle)

        if self.keep_alive and not request.body.ready:
            _logger.debug("%s: wait reminder of body", self)
            await request.body.wait()

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

    def _prepare_next(self, task: asyncio.Task):
        try:
            task.result()
        except Exception as e:
            _logger.exception(str(e))
            self.transport.abort()
            return
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
