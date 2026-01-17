from __future__ import annotations

import contextvars
import functools
import io
import os
import re
import tempfile
import typing
from urllib.parse import parse_qsl, unquote

from . import config
from .base_protocol import ReadBuffer
from .http_util import RE_HEADER, RE_START_LINE, MultiValuePreference
from .session import Session

if typing.TYPE_CHECKING:
    from .router import HttpStrategy

RE_CHUNK = re.compile(r'(Psize?[0-9A-F])(\s*;\s*%s(\s*=\s*.?+))*')


class HttpSyntaxError(Exception):
    pass


class Request:
    def __init__(self, strategy: HttpStrategy):
        self.strategy = strategy
        self.ready: bool = False
        self.method: str = b""
        self.target: str = ""
        self._query: bytes = b""
        self.http_version: typing.Literal[b'1.1', b'1.0', b''] = b''
        self.headers: dict[bytes, bytes] = {}
        self.body: RequestBody = _EMPTY_BODY
        self.context = contextvars.copy_context()

    def _receive_data(self, buf: ReadBuffer) -> None:
        if self.ready:
            self.body.receive_data(buf)
            return

        MAX_LINE_LENGTH = config.MAX_LINE_LENGTH  # noqa: N806
        # parse the first line
        if not self.method:
            line = buf.read_line(MAX_LINE_LENGTH)
            if line is None:
                return
            m = RE_START_LINE.fullmatch(line)
            if m is None:
                raise HttpSyntaxError("Syntax error in first line")
            method, target, query, http_version = m.groups()
            self.http_version = http_version
            if http_version not in (b'1.1', b'1.0'):
                raise ValueError(f"Invalid HTTP version {http_version!r}")
            if target[:1] not in (b'/', b'*'):
                raise ValueError(f"Invalid target {target!r}")
            self.target = unquote(target)
            self.query = query or b''
            self.method = method.decode()

        # parse headers
        while True:
            line = buf.read_line(MAX_LINE_LENGTH)
            if line is None:
                return
            if not line:
                break
            m = RE_HEADER.fullmatch(line)
            if m is None:
                raise HttpSyntaxError("Syntax error in header")
            self._set_header(*m.groups())

        # host is required (resolve it)
        if not self.host:
            raise HttpSyntaxError("missing host header")
        # set the body
        if value := self.headers.get(b'transfer-encoding'):
            if self.headers.get(b'content-length'):
                raise HttpSyntaxError("Provided both transfer-encoding and content-length.")
            if value != b'chunked':
                raise NotImplementedError("Only 'chunked' transfer-encoding is supported.")
            self.body = ChunkedBodyReceiver()
        elif value := self.headers.get(b'content-length'):
            size = int(value.decode())
            if size > 0:
                if size <= self.strategy.max_memory_receiver:
                    self.body = BodyReceiver(size)
                else:
                    self.body = BodyFileReceiver(size, self.strategy)
        self.ready = True
        self.body.receive_data(buf)

    def _set_header(self, key: bytes, value: bytes):
        key = key.lower()
        if key == b'cookie':
            # TODO harden
            for cookie_pair in value.split(b';'):
                cookie_name, _eq, cookie_value = cookie_pair.partition(b'=')
                self.cookies[cookie_name] = cookie_value
            return
        if old_value := self.headers.get(key):
            value = old_value + b', ' + value
        self.headers[key] = value

    @functools.cached_property
    def scheme(self):
        if (proto := self.headers.get(b'x-forwarded-proto')) and proto.decode() == 'https':
            return 'https'
        return 'http'

    @functools.cached_property
    def host(self):
        return self.headers[b'host'].decode()

    @functools.cached_property
    def absolute_target(self):
        return f"{self.scheme}://{self.host}{self.target.decode()}"

    @functools.cached_property
    def absolute_target_url(self):
        target = self.absolute_target
        if self._query:
            target = f"{target}.{self._query.decode()}"
        return target

    @functools.cached_property
    def _path_route(self):
        target = unquote(self.target)
        if forwarded_prefix := self.headers.get(b'x-forwarded-prefix'):
            prefix = unquote(forwarded_prefix)
            if not target.startswith(prefix):
                # Invalid path
                raise RuntimeError  # 400 bad request
            target = target[len(prefix) :]
        parts = target.split('/')
        parts.reverse()
        if parts.pop():
            # invalid path
            parts.clear()  # raise 400 bad request
        elif len(parts) == 1 and not parts[0]:
            parts.clear()  # the path is just "/"
        return parts

    @functools.cached_property
    def path_params(self):
        return {}

    @functools.cached_property
    def query_params(self):
        params = {}
        for key, value in parse_qsl(self._query.decode(), keep_blank_values=True):
            params.setdefault(key, value)
        return params

    @functools.cached_property
    def accept(self):
        # accepted mime-types
        return MultiValuePreference(self.headers.get(b'accept'))

    @functools.cached_property
    def accept_language(self):
        return MultiValuePreference(self.headers.get(b'accept-language'))

    @functools.cached_property
    def accept_encoding(self):
        return MultiValuePreference(self.headers.get(b'accept-encoding'))

    @functools.cached_property
    def cookies(self) -> dict[bytes, bytes]:
        return {}

    @functools.cached_property
    def user_authentication(self) -> typing.Any:
        return self.strategy.authenticate(self)

    @functools.cached_property
    def user_session(self) -> Session:
        sid = self.cookies.get(b'SESSION_ID')
        return self.strategy.session(sid)

    def __repr__(self):
        method = self.method
        if self.ready:
            target = self.absolute_target
        else:
            target = '(waiting) ' + self.target
        return f"Request({method} {target})"


class RequestBody:
    _resume_callback = None
    ready: bool
    size: int

    def receive_data(self, buf: ReadBuffer):
        raise NotImplementedError

    def receive_paused(self, resume_callback):
        assert not self.ready
        self._resume_callback = resume_callback

    async def wait(self):  # FIXME Awaitable?
        ...

    def close(self):
        pass

    def open(self): ...

    def read(self):
        with self.open() as f:
            return f.read()


class NoRequestBody(RequestBody):
    ready = True
    size = 0

    def __bool__(self):
        return False

    def receive_data(self, buf):
        pass


_EMPTY_BODY = NoRequestBody()


class ChunkedBodyReceiver(RequestBody):
    def __init__(self):
        self.__length = 0
        self.__reading: typing.Literal['chunk', 'trailer', 'done'] = 'chunk'
        self.__receiver = BodyFileReceiver(-1, None)

    @property
    def ready(self):
        return self.__reading == 'done'

    @property
    def size(self):
        return -1

    def open(self):
        return self.__receiver.open()

    def receive_data(self, buf: ReadBuffer):
        MAX_LINE_LENGTH = config.MAX_LINE_LENGTH  # noqa: N806
        while self.__reading == 'chunk':
            line = buf.read_line(MAX_LINE_LENGTH)
            if line is None:
                return
            m = RE_CHUNK.fullmatch(line)
            if not m:
                raise HttpSyntaxError("Syntax error in chunk")

            size = m.group(0)
            # FIXME parse and validate size (hexa)
            if size:
                self.__receiver.receive_exactly(buf, size)  # FIXME not implemented yet
                if buf.read_line(MAX_LINE_LENGTH):
                    raise HttpSyntaxError("Syntax error in chunk body")
                self.__length += size
            else:
                self.__reading = 'trailer'
                self.__receiver.remaining = 0

        while self.__reading == 'trailer':
            line = buf.read_line(MAX_LINE_LENGTH)
            if not line:
                break
            m = RE_HEADER.fullmatch(line)
            if not m:
                raise HttpSyntaxError("Syntax error in trailer")
            key, value = m.groups()
            key = key.decode('ascii').lower()
            value = value.decode()
            self._set_trailer(key, value)

        self.__reading = 'done'

    def _set_trailer(self, key, value):
        # ignoring trailers in this implementation
        pass


class BodyReceiver(RequestBody):
    def __init__(self, expected_size):
        assert expected_size > 0
        self.size = expected_size
        self.__data = bytearray(expected_size)
        self.__receiving = memoryview(self.__data)

    @property
    def ready(self):
        return self.__receiving is None

    def receive_data(self, buf: ReadBuffer):
        recv = self.__receiving
        assert recv is not None
        view = buf.read(len(recv))
        count = len(view)
        assert count > 0
        recv[:count] = view
        if len(recv) > count:
            self.__receiving = recv[count:]
        else:
            self.__receiving = None


class BodyFileReceiver(RequestBody):
    def __init__(self, expected_size):
        self.size = self.remaining = expected_size
        self.__fd = tempfile.SpooledTemporaryFile()  # noqa: SIM115

    def __del__(self):
        self.__fd.close()

    @property
    def ready(self):
        return self.remaining == 0

    def receive_data(self, buf: ReadBuffer):
        if self.ready:
            return
        view = buf.read(self.remaining)
        self.__fd.write(view)
        self.remaining -= len(view)

    def open(self):
        assert self.ready
        fd = self.__fd
        if not fd.name:
            # still in memory, read all
            fd.seek(0)
            return io.BytesIO(fd.read())
        fd.flush()
        return os.fdopen(fd.fileno(), 'rb')
