import pytest

from mabeyn.guard.pii import iban_valid, luhn_valid, mask_pii, national_id_valid

IBAN = "TR33 0006 1005 1978 6457 8413 26"
CARD = "4111 1111 1111 1111"
NATIONAL_ID = "10000000146"


def test_validators():
    assert iban_valid(IBAN) and iban_valid(IBAN.replace(" ", ""))
    assert not iban_valid("TR33 0006 1005 1978 6457 8413 27")
    assert luhn_valid(CARD.replace(" ", "")) and not luhn_valid("4111111111111112")
    assert national_id_valid(NATIONAL_ID) and not national_id_valid("12345678901")
    assert not national_id_valid("00000000000")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"Your IBAN is {IBAN}.", "iban"),
        (f"Card {CARD} was charged.", "card"),
        (f"Card {CARD.replace(' ', '-')} was charged.", "card"),
        (f"Kimlik no {NATIONAL_ID} ile kayıtlı.", "national_id"),
        ("Write to ayse.yilmaz@example.com please.", "email"),
        ("Call 0532 123 45 67 tomorrow.", "phone"),
        ("Call +90 532 123 45 67 tomorrow.", "phone"),
        ("Reach me on +44 20 7946 0958.", "phone"),
    ],
)
def test_personal_data_is_masked(text, kind):
    result = mask_pii(text)
    assert result.kinds == [kind] and result.changed
    assert "[" in result.text
    for secret in (IBAN, CARD, NATIONAL_ID, "ayse.yilmaz", "532", "7946"):
        assert secret not in result.text or secret not in text


@pytest.mark.parametrize(
    "text",
    [
        "Order KS-1001 total is 899.90 TL, tracking number AK100200.",
        "Your parcel KS-2002 was delivered on 2026-10-24.",
        "The refund of 2499.0 TL takes 5 to 7 business days.",
        "Version 12345678901 is not an identity number.",
        "Call us between 09:00 and 18:00 on weekdays.",
        "Postal code 34710, Atatürk Cad. 12/3.",
        "Card ending 1111 was used.",
    ],
)
def test_ordinary_numbers_are_left_alone(text):
    result = mask_pii(text)
    assert result.text == text and not result.changed


def test_several_kinds_in_one_text():
    result = mask_pii(f"{IBAN} and {CARD} and me@example.com")
    assert result.kinds == ["iban", "email", "card"]
    assert result.text == "[iban] and [card number] and [email]"
