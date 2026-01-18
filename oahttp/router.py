from __future__ import annotations

import typing
from bisect import insort_right
from collections.abc import Callable, Coroutine

from . import config
from .http_connection import HttpConnection, Request
from .response import (
    FileBody,
    MethodNotAllowed,
    NotAcceptable,
    NotFound,
    Ok,
    Response,
    ServerErrorResponse,
    StaticBody,
)
from .session import Session

Dispatcher = Callable[[Request], Coroutine[typing.Any, typing.Any, Response]]

NOT_FOUND = NotFound()


def _key_prio(t):
    return -t[0]


async def default_dispatcher(request: Request) -> Response:
    if request.method == 'OPTIONS' and request.target == '*':
        response = Ok(None)
        response.headers[b'allow'] = b'GET, HEAD, OPTIONS'
        return response

    if request.method == 'TRACE':
        if not config.TRACE_METHOD_ENABLED:
            raise NotImplementedError("TRACE not implemented")
        response = Ok(request)  # TODO serialize request
        response.headers['content-type'] = b'message/http'
        return response

    return NOT_FOUND


class HttpStrategy:
    debug = False  # TODO use this instead of global config?

    def __init__(self):
        self.dispatcher = MutliDispatcher()
        self.dispatcher.add(default_dispatcher, 0.1)

    def session(self, sid) -> Session: ...

    def authenticate(self, request: Request) -> typing.Any:
        ...
        return None

    def wrap_error(self, request: Request, exception: Exception) -> Response:
        if isinstance(exception, Response):
            return exception
        return ServerErrorResponse(exception)

    def new_connection(self) -> HttpConnection:
        return HttpConnection(self)

    def make_response(self, request: Request, content):
        if not content and isinstance(content, (bytes, str, type(None))):
            return StaticBody(b'')
        if not isinstance(content, str):
            # TODO make json response
            content = str(content)
        return StaticBody(content.encode())

    def route(self, method: str | list[str], path: str):
        if isinstance(method, str):
            method = [method]
        assert method, "Missing method"

        def bind_route(func, /):
            other = func
            if method != ['*']:
                md = MethodDispatcher()
                for m in method:
                    md.methods[m] = other
                other = md

            self.dispatcher = PathDispatcher.build(self.dispatcher, path, other)
            return func

        return bind_route


class MergableDispatcher(Dispatcher):
    def merge(self, other: Dispatcher) -> MergableDispatcher:
        return MutliDispatcher(self, other)

    async def __call__(self, request: Request) -> Response:
        raise NotImplementedError


class MutliDispatcher(MergableDispatcher):
    def __init__(self, *dispatchers):
        self._dispatchers = list(zip([1.0] * len(dispatchers), dispatchers))

    def merge(self, other):
        if isinstance(other, MutliDispatcher):
            for priority, d in other._dispatchers:
                self.add(d, priority)
            return self
        for i, (p, d) in enumerate(self._dispatchers):
            if other is d:
                return self
            if p == 1.0 and isinstance(d, MergableDispatcher) and type(other) is type(d):
                self._dispatchers[i] = (p, d.merge(other))
                return self
        self.add(other)
        return self

    @staticmethod
    def build(dispatcher, other):
        if isinstance(dispatcher, MergableDispatcher):
            return dispatcher.merge(other)
        return MutliDispatcher(dispatcher, other)

    def add(self, other, priority=1.0):
        insort_right(self._dispatchers, (priority, other), key=_key_prio)

    async def __call__(self, request: Request):
        for _p, dispatcher in self._dispatchers:
            response = await dispatcher(request)
            if response is not NOT_FOUND:
                return response
        return NOT_FOUND


class PathDispatcher(MergableDispatcher):
    def __init__(self):
        self.static = {}
        self.dynamic = []
        self.root = None

    def merge(self, other):
        if isinstance(other, PathDispatcher):
            for p, f in other.static.items():
                if g := self.static.get(p):
                    f = MutliDispatcher.build(g, f)
                self.static[p] = f
            for d in other.dynamic:
                insort_right(self.dynamic, d, key=_key_prio)
            other = other.root
            if other is None:
                return self
        if self.root is None:
            self.root = other
        else:
            self.root = MutliDispatcher.build(self.root, other)
        return self

    @staticmethod
    def build(dispatcher, path, other):
        path_parts = path.split('/')
        path_parts.reverse()
        if path_parts.pop():
            raise ValueError("path should start with '/'")
        out = other
        while path_parts:
            part = path_parts.pop()
            assert part
            if part == '...':
                break
            d = PathDispatcher()
            if part[0] == '<' and part[-1] == '>':
                priority = 1
                matcher = None
                insort_right(
                    d.dynamic,
                    (priority, part[1:-1], matcher, out),
                    key=_key_prio,
                )
            else:
                d.static[part] = out
            out = d
        return MutliDispatcher.build(dispatcher, out)

    async def __call__(self, request: Request):
        if path_parts := request._path_route:
            part = path_parts.pop()
            if func := self.static.get(part):
                response = await func(request)
                if response is not NOT_FOUND:
                    return response
            if part == '.' or not part:
                response = await func(request)
                if response is not NOT_FOUND:
                    return response
            if part == '..':
                return NotFound()  # path traversal, stop

            for _prio, param_name, matcher, func in self.dynamic:
                value = part
                if matcher is not None:
                    value = matcher(value)
                    if value is None:
                        continue
                old_value = request.path_params.get(param_name)
                try:
                    request.path_params[param_name] = value
                    response = await func(request)
                    if response is not NOT_FOUND:
                        return response
                finally:
                    if old_value is None:
                        request.path_params.pop(param_name, None)
                    else:
                        request.path_params[param_name] = old_value

            path_parts.append(part)
        elif func := self.root:
            return await func(request)
        return NOT_FOUND


class MethodDispatcher(MergableDispatcher):
    def __init__(self):
        self.methods = {}

    def merge(self, other):
        if isinstance(other, MethodDispatcher):
            methods = other.methods.copy()
            methods.update(self.methods)
            self.methods = methods
            return self
        return super().merge(other)

    async def __call__(self, request: Request):
        if func := self.methods.get(request.method):
            return await func(request)
        if request.method == 'HEAD' and (func := self.methods.get('GET')):
            return await self.__head_fallback(request, func)
        return MethodNotAllowed(request.method, list(self.methods))

    async def __head_fallback(self, request: Request, func: Dispatcher) -> Response:
        request.method = 'GET'
        try:
            response = await func(request)
        finally:
            request.method = 'HEAD'
        # remove the body
        response.content = None
        return response


class ContentTypeDispatcher(MergableDispatcher):
    def __init__(self):
        self.content_types: dict[str, Dispatcher] = {}

    def merge(self, other):
        if isinstance(other, ContentTypeDispatcher):
            map = other.content_types.copy()
            map.update(self.content_types)
            self.content_types = map
            return self
        return super().merge(other)

    async def __call__(self, request: Request):
        accept = request.accept
        prio_func = [
            (priority, func)
            for content_type, func in self.content_types.items()
            if (priority := accept.acceptable(content_type))
        ]
        if not prio_func:
            return NotAcceptable()
        prio_func.sort(key=_key_prio)
        func = prio_func[0][1]
        return func(request)


class FileDispatcher(Dispatcher):
    def __init__(self, root_path):
        from pathlib import Path

        self.root_path = Path(root_path)

    async def __call__(self, request: Request):
        # TODO add accept-ranges header
        # TODO add x-accel-redirect
        if request.method not in ('GET', 'HEAD'):
            raise MethodNotAllowed(request.method, ['GET', 'HEAD'])

        path = self.root_path
        for part in reversed(request._path_route):
            path /= part
        fp = None
        try:
            fp = path.open('rb')
        except OSError:
            return NOT_FOUND
        else:
            if request.method == 'GET':
                body = FileBody(fp)
                fp = None  # keep open
            else:
                body = None
            return Ok(body)
        finally:
            if fp is not None:
                fp.close()
