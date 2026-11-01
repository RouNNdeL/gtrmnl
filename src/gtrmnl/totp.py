"""Time-based one-time passwords (RFC 6238) for Garmin's authenticator-app MFA.

Standard library only. Accepts the base32 secret an authenticator app is
set up with (spaces and lower case allowed) or the full otpauth:// URI from
the QR code.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

_ALGOS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class TotpError(ValueError):
    """The secret cannot be used. Never includes the secret itself."""


@dataclass(frozen=True)
class Totp:
    key: bytes = field(repr=False)
    digits: int = 6
    period: int = 30
    algorithm: str = "SHA1"

    @classmethod
    def parse(cls, text: str) -> Totp:
        text = text.strip()
        digits, period, algo = 6, 30, "SHA1"
        secret = text
        if text.lower().startswith("otpauth://"):
            u = urlparse(text)
            if u.netloc.lower() != "totp":
                raise TotpError("only otpauth://totp/ URIs are supported")
            q = {k.lower(): v[-1] for k, v in parse_qs(u.query).items()}
            secret = unquote(q.get("secret", ""))
            try:
                digits = int(q.get("digits", 6))
                period = int(q.get("period", 30))
            except ValueError:
                raise TotpError("otpauth URI has a non-numeric digits or period") from None
            algo = q.get("algorithm", "SHA1").upper()
        if algo not in _ALGOS:
            raise TotpError(f"unsupported TOTP algorithm {algo}")
        if not 6 <= digits <= 10 or period <= 0:
            raise TotpError("TOTP digits must be 6-10 and the period positive")
        clean = secret.replace(" ", "").replace("-", "").upper()
        if not clean:
            raise TotpError("empty TOTP secret")
        try:
            key = base64.b32decode(clean + "=" * (-len(clean) % 8))
        except (binascii.Error, ValueError):
            raise TotpError("the TOTP secret is not valid base32") from None
        return cls(key, digits, period, algo)

    def at(self, t: float) -> str:
        counter = int(t // self.period)
        mac = hmac.new(self.key, struct.pack(">Q", counter), _ALGOS[self.algorithm]).digest()
        off = mac[-1] & 0x0F
        code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 10**self.digits
        return str(code).zfill(self.digits)

    def fresh(self, now: float | None = None, margin: float = 5.0, sleep=time.sleep) -> str:
        """A code with at least `margin` seconds of validity left.

        Near the end of a period it waits for the next one, so the code does
        not expire between being generated and Garmin checking it.
        """
        now = time.time() if now is None else now
        left = self.period - (now % self.period)
        if left < margin:
            sleep(left + 0.5)
            now += left + 0.5
        return self.at(now)
