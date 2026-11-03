from mabeyn.auth.api_keys import PREFIXES, generate_api_key, hash_api_key
from mabeyn.auth.passwords import hash_password, verify_dummy, verify_password


def test_password_hash_verifies():
    hashed = hash_password("correct horse")
    assert hashed != "correct horse"
    assert verify_password(hashed, "correct horse")
    assert not verify_password(hashed, "wrong")


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_does_not_verify():
    assert not verify_password("not-a-hash", "anything")


def test_dummy_verify_does_not_raise():
    verify_dummy("anything")


def test_api_key_shape_and_hash():
    key = generate_api_key()
    assert key.plain.startswith(PREFIXES["secret"])
    assert key.plain.startswith(key.prefix)
    assert key.key_hash == hash_api_key(key.plain)
    assert key.plain not in key.key_hash


def test_api_keys_are_unique():
    assert generate_api_key().plain != generate_api_key().plain


def test_the_prefix_shows_what_a_key_may_do():
    assert generate_api_key("publishable").plain.startswith("mbn_pub_")
    assert generate_api_key("secret").prefix.startswith("mbn_sec_")
