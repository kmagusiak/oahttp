from __future__ import annotations

import codecs
import datetime
import email.message
import email.utils
import fnmatch
import re

# TODO transfer-encoding

# TODO perf: replace regex with find and translate when possible?
RE_START_LINE = re.compile(rb"([A-Z]+) (.*?)(?:\?(.*))? HTTP/(\d\.\d)$")
TOKEN = rb'[-!#$%&\'*+.^_`|~0-9a-zA-Z]+'
RE_TOKEN = re.compile(TOKEN)
RE_HEADER = re.compile(rb'(%s):\s*(.*)\s*$' % TOKEN, re.IGNORECASE)


def parse_key_value2(s):
    # FIXME overkill?
    msg = email.message.Message()
    msg['X'] = s  # parse
    return msg.get_params()


def parse_key_value(s):
    key, _eq, value = s.partition('=')
    value = codecs.decode(value, 'unicode-escape')
    return key, value


def format_date_time(dt: datetime.datetime) -> bytes:
    """Generate a RFC 7231 / RFC 9110 IMF-fixdate string"""
    return email.utils.format_datetime(dt.astimezone(datetime.UTC), usegmt=True).encode()


def parse_date_time(val: bytes) -> datetime.datetime:
    # https://www.rfc-editor.org/rfc/rfc9110#name-date-time-formats
    raise NotImplementedError


class MultiValuePreference:
    # typical usage for accept headers
    # Accept, Accept-Encoding, Accept-Language, TE
    def __init__(self, value: str | bytes | None = None):
        self.options = []
        if value:
            if not isinstance(value, str):
                value = value.decode()
            for option in value.split(','):
                option_parts = option.split(';')
                option_parts.reverse()
                key = option_parts.pop().strip()
                priority = 1.0
                while option_parts:
                    param = option_parts.pop().strip()
                    if param.startswith('q='):
                        try:
                            priority = float(param[2:])
                        except ValueError:
                            pass  # ignore invalid quality
                self.options.append((key, priority))

    def acceptable(self, content_type: str) -> float:
        for key, priority in self.options:
            if key == content_type or fnmatch.fnmatchcase(content_type, key):
                return priority
        return 0.0

    def __repr__(self):
        return f"MultiValuePref({self})"

    def __str__(self):
        return ','.join(
            key + (f';q={priority}' if priority != 1.0 else '')  #
            for key, priority in self.options
        )


class Cookie:  # this is for set-cookie only
    # https://datatracker.ietf.org/doc/html/rfc6265
    def __init__(self, name: str, value: bytes):
        self.name = name
        self.value = value
        self.quoted = False
        # Expires=date
        # Max-Age=number
        # Domain=...
        # Path=/
        self.secure = False  # Secure
        self.http_only = False  # HttpOnly

    def generate_set_cookie(self) -> bytes:
        raise NotImplementedError
