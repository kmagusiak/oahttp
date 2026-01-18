import asyncio
from collections.abc import Buffer


class BufferError(Exception):
    pass


class ReadBuffer(Buffer):
    __slots__ = ('__buffer', '__pos', '__pos_line', '__until', '__view')

    def __init__(self, size: int, init_bytes: Buffer = b''):
        assert size > 9, 'buffer too small'
        self.__buffer = array = bytearray(size)
        self.__view = memoryview(array)
        self.__pos = self.__until = self.__pos_line = 0

        if init_bytes:
            init_bytes = memoryview(init_bytes)
            bs = len(init_bytes)
            assert bs <= size, f'Init buffer too big ({bs}), capacity {size}'
            array[:bs] = init_bytes
            self.__until = bs

    @property
    def total_size(self):
        return len(self.__buffer)

    def write_buffer(self, size_hint: int = -1) -> memoryview:
        view = self.__view
        until = self.__until
        total_size = len(self.__buffer)
        if not (0 < size_hint < total_size):
            size_hint = total_size // 4
        if until + size_hint > total_size:
            # re-align
            pos = self.__pos
            if pos > 0:
                cur_size = until - pos
                view[:cur_size] = view[pos:until]  # XXX check if ok in python  # noqa: FIX003
                self.__pos = self.__pos_line = 0
                self.__until = until = cur_size
            elif until >= total_size:
                raise BufferError("buffer full")

        return view[until:]

    def written(self, count: int) -> None:
        self.__until += count

    @property
    def empty(self) -> bool:
        return self.__pos == self.__until

    @property
    def full(self) -> bool:
        return self.__pos == 0 and self.__until == len(self.__buffer)

    def __buffer__(self, flags):
        return self.__view[self.__pos : self.__until]

    def read_line(self, limit: int = 10**6) -> memoryview | None:
        pos = self.__pos
        until = min(pos + limit, self.__until)
        # find CR? LF
        buf = self.__buffer
        lf = buf.find(b'\n', self.__pos_line, until)
        if lf < 0:
            if until != self.__until:
                raise BufferError("limit reached")
            self.__pos_line = max(pos, self.__until - 1)
            return None
        self.__pos = self.__pos_line = lf + 1
        cr = lf - 1
        if not (cr >= 0 and buf[cr] == 13):
            cr = lf
        return self.__view[pos:cr]

    def read(self, nbytes: int = -1) -> memoryview:
        pos = self.__pos
        count = self.__until - pos
        if 0 < nbytes < count:
            count = nbytes
        self.__pos += count
        self.__pos_line = self.__pos
        return self.__view[pos : pos + count]

    def __repr__(self):
        return f"ReadBuffer({self.__pos}-{self.__until}, size: {len(self.__buffer)})"


class BaseBufferedProtocol(asyncio.BufferedProtocol):
    def __init__(self, *, buffer_size: int):
        super().__init__()
        self._read_buffer = ReadBuffer(buffer_size)
        self._transport: asyncio.Transport | None = None
        self.__write_allowed = asyncio.Event()
        self.__write_allowed.set()

    def connection_made(self, transport):
        self._transport: asyncio.Transport = transport  # type: ignore

    def connection_lost(self, exc):
        self._transport = None

    def get_buffer(self, sizehint):
        return self._read_buffer.write_buffer(sizehint)

    def buffer_updated(self, nbytes):
        self._read_buffer.written(nbytes)

    def pause_writing(self):
        self.__write_allowed.clear()

    def resume_writing(self):
        self.__write_allowed.set()

    async def write_throttle(self):
        await self.__write_allowed.wait()
