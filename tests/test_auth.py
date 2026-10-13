import base64
import time

import pytest
from cryptography.fernet import Fernet

from kaneo_templates.auth import CookieCipher, DeviceState, _basic_auth_ok

SECRET = "k" * 32


def test_cipher_round_trip():
    cipher = CookieCipher(SECRET)
    sealed = cipher.seal({"token": "abc"})
    assert "abc" not in sealed
    assert cipher.open(sealed, 60) == {"token": "abc"}


def test_cipher_is_deterministic_per_secret():
    sealed = CookieCipher(SECRET).seal({"token": "abc"})
    assert CookieCipher(SECRET).open(sealed, 60) == {"token": "abc"}


def test_cipher_rejects_other_secret():
    sealed = CookieCipher(SECRET).seal({"token": "abc"})
    assert CookieCipher("z" * 32).open(sealed, 60) is None


def test_cipher_key_is_derived_not_raw_secret():
    # A Fernet key built directly from the secret bytes must not open the cookie.
    raw = Fernet(base64.urlsafe_b64encode(SECRET.encode()[:32]))
    sealed = CookieCipher(SECRET).seal({"token": "abc"})
    with pytest.raises(Exception):  # noqa: B017 - any failure is fine
        raw.decrypt(sealed.encode())


@pytest.mark.parametrize("value", [None, "", "garbage", "gAAAAA" + "x" * 80])
def test_cipher_rejects_invalid_values(value):
    assert CookieCipher(SECRET).open(value, 60) is None


def test_cipher_rejects_tampered_value():
    cipher = CookieCipher(SECRET)
    sealed = cipher.seal({"token": "abc"})
    tampered = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
    assert cipher.open(tampered, 60) is None


def test_cipher_rejects_expired_value():
    cipher = CookieCipher(SECRET)
    old = cipher._fernet.encrypt_at_time(b'{"token": "abc"}', int(time.time()) - 120)
    assert cipher.open(old.decode(), 60) is None
    assert cipher.open(old.decode(), 600) == {"token": "abc"}


def test_cipher_rejects_non_object_payload():
    cipher = CookieCipher(SECRET)
    sealed = cipher._fernet.encrypt(b'["token"]').decode()
    assert cipher.open(sealed, 60) is None


def test_device_state_expiry():
    now = time.time()
    assert not DeviceState("d", "U", "https://k/device", 5, now + 60).expired
    assert DeviceState("d", "U", "https://k/device", 5, now - 1).expired


def b64(text):
    return "Basic " + base64.b64encode(text.encode()).decode()


@pytest.mark.parametrize(
    "header, ok",
    [
        (b64("admin:pw"), True),
        (b64("admin:pw:with:colons"), False),
        (b64("admin:wrong"), False),
        (b64("other:pw"), False),
        (b64("adminpw"), False),
        ("Basic not-base64!!", False),
        ("Bearer abc", False),
        ("", False),
        (None, False),
        ("basic " + base64.b64encode(b"admin:pw").decode(), True),
    ],
)
def test_basic_auth_header_parsing(header, ok):
    assert _basic_auth_ok(header, "admin", "pw") is ok


def test_basic_auth_password_may_contain_colons():
    assert _basic_auth_ok(b64("admin:a:b"), "admin", "a:b")


def test_basic_auth_utf8_credentials():
    assert _basic_auth_ok(b64("élodie:mot-de-passe-é"), "élodie", "mot-de-passe-é")
