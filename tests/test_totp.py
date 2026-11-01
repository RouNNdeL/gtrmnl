import pytest

from gtrmnl.totp import Totp, TotpError

# RFC 6238 appendix B: ASCII secrets, 8 digits.
SHA1_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
SHA256_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA"
VECTORS = [
    (59, "94287082", "46119246"),
    (1111111109, "07081804", "68084774"),
    (1111111111, "14050471", "67062674"),
    (1234567890, "89005924", "91819424"),
    (2000000000, "69279037", "90698825"),
    (20000000000, "65353130", "77737706"),
]


@pytest.mark.parametrize(("t", "sha1", "sha256"), VECTORS)
def test_rfc6238_vectors(t, sha1, sha256):
    assert Totp.parse(f"otpauth://totp/x?secret={SHA1_B32}&digits=8").at(t) == sha1
    uri = f"otpauth://totp/x?secret={SHA256_B32}&digits=8&algorithm=SHA256"
    assert Totp.parse(uri).at(t) == sha256


def test_plain_secret_six_digits_and_formatting():
    t = Totp.parse(SHA1_B32.lower()[:16] + " " + SHA1_B32.lower()[16:])
    assert t.digits == 6 and t.at(59) == "287082"  # last 6 digits of the 8-digit vector


def test_fresh_waits_out_a_nearly_expired_period():
    t = Totp.parse(SHA1_B32)
    slept = []
    code = t.fresh(now=58.0, margin=5, sleep=slept.append)  # 2 s left in the period
    assert slept == [2.5] and code == t.at(60.5)
    slept.clear()
    assert t.fresh(now=31.0, sleep=slept.append) == t.at(31.0) and slept == []


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not base32!",
        "otpauth://hotp/x?secret=GEZDGNBV",
        "otpauth://totp/x?secret=GEZDGNBV&algorithm=MD5",
        "otpauth://totp/x?secret=GEZDGNBV&digits=4",
    ],
)
def test_bad_secrets_rejected_without_echoing(bad):
    with pytest.raises(TotpError) as e:
        Totp.parse(bad)
    assert "GEZDGNBV" not in str(e.value)


def test_key_not_in_repr():
    r = repr(Totp.parse(SHA1_B32))
    assert "1234567890" not in r and SHA1_B32 not in r and "key" not in r
