from __future__ import annotations

import asyncio
import datetime
import io
import os
import typing
from collections.abc import Buffer

from . import config
from .http_util import Cookie, format_date_time, guess_mimetype

if typing.TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from .request import Request


class Response:
    status: bytes
    http_version: bytes = b''

    def __init_subclass__(cls):
        if status := getattr(cls, 'status', None):
            assert isinstance(status, bytes) and status[3] == 32, f'{status!r}'
            assert 100 <= int(status[:3].decode()) <= 999

    def __init__(self, content: typing.Any = None):
        assert self.status, "missing status"
        self.headers: dict[bytes, bytes] = {
            b'date': format_date_time(datetime.datetime.now()),
            b'server': config.ORIGIN,
        }
        self.set_cookies: dict[str, Cookie] = {}
        self.content = content
        self.body: ResponseBody | None = None

    def set_header(self, key: bytes, value: str | bytes) -> None:
        assert key != b'set-cookie', "use Response.set_cookies directly"
        if not value:
            self.headers.pop(key, None)
            return
        if isinstance(value, str):
            value = value.encode()
        self.headers[key] = value

    def _for_request(self, request: Request):
        assert request.ready, "Request is not ready"
        assert not self.ready, "Response is already generated"
        self.http_version = request.http_version
        content = self.content
        if not self.content:
            self.body = EmptyResponseBody()
        elif isinstance(content, ResponseBody):
            self.body = content
        elif isinstance(content, Buffer):
            self.body = StaticBody(content)
        else:
            self.body = request.serialize_response(content)

    @property
    def ready(self):
        return self.body is not None

    async def send(self, request: Request, transport: asyncio.WriteTransport, throttle):
        self._for_request(request)
        self.body.set_headers(self.headers)
        transport.write(self._generate_header())
        await self.body.send(transport, throttle)

    @typing.final
    def send_immediately(self, request: Request, transport: asyncio.WriteTransport):
        async def no_wait():
            pass

        loop = asyncio.get_running_loop()
        loop.run_until_complete(loop.create_task(self.send(request, transport, no_wait)))

    def _generate_header(self):
        assert self.http_version
        if config.DEBUG:
            from .http_util import RE_HEADER, RE_TOKEN

            assert all(
                isinstance(key, bytes)
                and isinstance(value, bytes)
                and RE_HEADER.fullmatch(key + b': ' + value)
                for key, value in self.headers.items()
            )
            assert all(
                name == cookie.name and RE_TOKEN.fullmatch(name)
                for name, cookie in self.set_cookies.items()
            )
        response = [
            b'HTTP/',
            self.http_version,
            b' ',
            self.status,
            b'\r\n',
        ]
        for key, value in self.headers.items():
            response.extend((key, b': ', value, b'\r\n'))
        for cookie in self.set_cookies.values():
            response.extend((b'set-cookie: ', cookie.generate_set_cookie(), b'\r\n'))
        response.append(b'\r\n')
        return b''.join(response)


#######################################
# RESPONSES


class GenericResponse(Response):
    def __init__(
        self,
        body=None,
        *,
        status: bytes,
        content_type: bytes = b'text/html',
    ):
        self.status = status
        super().__init__(body)
        if body is not None:
            self.headers[b'content-type'] = content_type


class ServerErrorResponse(Response):
    status = b'500 Internal Server Error'

    def __init_subclass__(cls):
        status = cls.status
        assert 500 <= int(status[:2].decode()) <= 599

    def __init__(self, exception: Exception):
        super().__init__(exception)
        if isinstance(exception, NotImplementedError):
            self.status = b'501 Not Implemented'
        elif self.headers.get(b'retry-after'):
            self.status = b'503 Service Unavailable'


class RedirectResponse(Response):
    def __init__(self, location: bytes, *, permanent=False, can_change_method=False):
        if can_change_method:
            if permanent:
                status = b'301 Moved Permanently'
            else:
                status = b'302 Found'
                # could be a 303 when request is a POST and we want a GET afterwards
        else:
            if permanent:
                status = b'308 Permanent Redirect'
            else:
                status = b'307 Temporary Redirect'
        self.status = status
        super().__init__()
        self.headers[b'location'] = location


class Ok(Response):
    status = b'200 OK'

    def __init__(self, content):
        super().__init__(content)
        if content is None:
            self.status = b'204 No Content'


class Created(Response):
    status = b'201 Created'

    def __init__(self, content=None):
        super().__init__(content)
        if self.headers.get(b'location'):  # maybe
            self.status = b'303 See Other'


class NotModified(Response):
    status = b'304 Not Modified'
    # must generate
    # Content-Location, Date, ETag, and Vary
    # Cache-Control and Expires (see [CACHING])


class ClientErrorResponse(Response, Exception):
    status = b'400 Bad Request'

    def __init_subclass__(cls):
        status = cls.status
        assert 400 <= int(status[:3].decode()) <= 499


class Forbidden(ClientErrorResponse):
    status = b'403 Forbidden'


class NotFound(ClientErrorResponse):
    status = b'404 Not Found'

    def __init__(self, content=None, *, gone: bool = False):
        if gone:
            # permanently deleted
            self.status = b'410 Gone'
        super().__init__(content)


class MethodNotAllowed(ClientErrorResponse):
    status = b'405 Method Not Allowed'

    def __init__(self, method: str, allowed: list[str]):
        super().__init__(f"Method {method} not allowed")
        assert method not in allowed
        self.allowed = allowed
        self.headers[b'allow'] = ', '.join(self.allowed).encode()


class NotAcceptable(ClientErrorResponse):
    status = b'406 Not Acceptable'
    # negotation failed using accept* headers


class Conflict(ClientErrorResponse):
    status = b'409 Conflict'
    # lock issue, concurrent modification, etc.


class UnsupportedMediaType(ClientErrorResponse):
    status = b'415 Unsupported Media Type'
    # TODO add accept header


class ExpecationFailed(ClientErrorResponse):
    status = b'417 Expectation Failed'


class UpgradeRequired(ClientErrorResponse):
    status = b'426 Upgrade Required'

    def __init__(self, content=None, *, acceptable: bytes):
        super().__init__(content)
        self.headers[b'upgrade'] = acceptable


class ContinueResponse(Response):
    status = b'100 Continue'

    def __init__(self):
        super().__init__()


class UpgradeResponse(Response):
    status = b'101 Switching Protocols'

    def __init__(self, new_protocol: asyncio.BaseProtocol, name: str = ''):
        super().__init__()
        self.new_protocol = new_protocol
        self.headers[b'connection'] = b'upgrade'
        self.headers[b'upgrade'] = (name or new_protocol.protocol_name).encode()


#######################################
# BODY


class ResponseBody:
    def set_headers(self, headers: dict[bytes, bytes]):
        pass

    async def send(self, transport: asyncio.WriteTransport, throttle: Callable[[], Coroutine]):
        raise NotImplementedError


class EmptyResponseBody(ResponseBody):
    def set_headers(self, headers: dict):
        assert b'content-length' not in headers

    async def send(self, transport, throttle):
        pass


class StaticBody(ResponseBody):
    def __init__(self, body: Buffer):
        self.__body = body if isinstance(body, bytes) else memoryview(body)

    def set_headers(self, headers: dict):
        if b'content-type' not in headers:
            if mime := guess_mimetype(self.__body):
                mime = mime.encode()
            headers[b'content-type'] = mime or b'stream/octect'
        headers[b'content-length'] = str(len(self.__body)).encode()

    async def send(self, transport, throttle):
        if self.__body:
            await throttle()
            transport.write(self.__body)


class FileBody(ResponseBody):
    def __init__(self, fd: io.FileIO):
        assert not fd.closed and fd.seekable() and 'b' in fd.mode
        self.__fd = fd

    def set_headers(self, headers: dict):
        if b'content-type' not in headers:
            if isinstance(file_name := self.__fd.name, str) and (mime := guess_mimetype(file_name)):
                mime = mime.encode()
            headers[b'content-type'] = mime or b'stream/octect'
        stat = os.fstat(self.__fd.fileno())
        headers[b'content-length'] = str(stat.st_size).encode()

    async def send(self, transport, throttle):
        await throttle()
        loop = asyncio.get_running_loop()
        fd = self.__fd
        fd.seek(0)
        try:
            await loop.sendfile(transport, fd)
        except NotImplementedError:
            # uvloop does not implement sendfile yet, manual fallback
            buf = memoryview(bytearray(4096))
            while count := fd.readinto(buf):
                await throttle()
                transport.write(buf[:count])

    def __del__(self):
        self.__fd.close()


class ChunkedBody(ResponseBody):
    def __init__(self, input_stream: AsyncIterator[bytes]):
        self.__input = input_stream

    def set_headers(self, headers: dict):
        headers[b'transfer-encoding'] = b'chunked'
        headers.setdefault(b'content-type', b'stream/octect')

    async def send(self, transport, throttle):
        async for data in self.__input:
            assert data, "no data returned by the iterator"
            await throttle()
            transport.write(len(data).hex())
            transport.write(b'\r\n')
            transport.write(data)
