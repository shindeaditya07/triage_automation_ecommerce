import pytest

from services.api.intake.pii import (
    AADHAAR,
    BANK_ACCOUNT,
    CARD,
    PAN,
    PLACEHOLDERS,
    UPI,
    luhn_valid,
    mask_pii,
    verhoeff_valid,
)

VISA = "4111111111111111"
VISA_SPACED = "4111 1111 1111 1111"
AMEX = "378282246310005"
MASTERCARD_BAD_CHECK = "5500 0000 0000 0005"  # grouped like a card, check digit wrong
AADHAAR_VALID = "234567890124"  # passes Verhoeff
AADHAAR_SPACED = "2345 6789 0124"


def test_the_checksums_agree_with_known_values() -> None:
    assert luhn_valid(VISA)
    assert not luhn_valid("4111111111111112")
    assert verhoeff_valid(AADHAAR_VALID)
    assert not verhoeff_valid("234567890125")


@pytest.mark.parametrize(
    "number",
    [VISA, VISA_SPACED, "4111-1111-1111-1111", AMEX, "3782 822463 10005", MASTERCARD_BAD_CHECK],
)
def test_card_numbers_are_masked(number: str) -> None:
    masked = mask_pii(f"I paid with {number}, please check.")
    assert masked.text == f"I paid with {PLACEHOLDERS[CARD]}, please check."
    assert masked.counts == {CARD: 1}
    assert number.replace(" ", "")[:12] not in masked.text.replace(" ", "")


def test_several_numbers_are_each_masked_and_counted() -> None:
    masked = mask_pii(f"first {VISA} second {AMEX}")
    assert masked.counts == {CARD: 2}
    assert VISA not in masked.text
    assert AMEX not in masked.text


@pytest.mark.parametrize(
    "text",
    [
        "tracking number 123456789012 (12 digits)",
        "AWB 12345678901234",
        "order KHG-10001 and KHG-20002",
        "call me on 9876543210 or +91 98765 43210",
        "my pincode is 560001, flat 4111",
        "paid ₹1,499.00 on 2026-10-15",
    ],
)
def test_numbers_that_are_not_ids_are_left_alone(text: str) -> None:
    masked = mask_pii(text)
    assert masked.text == text
    assert masked.counts == {}


@pytest.mark.parametrize("number", [AADHAAR_VALID, AADHAAR_SPACED, "2345-6789-0124"])
def test_aadhaar_numbers_are_masked(number: str) -> None:
    masked = mask_pii(f"my aadhaar is {number}.")
    assert masked.text == f"my aadhaar is {PLACEHOLDERS[AADHAAR]}."
    assert masked.counts == {AADHAAR: 1}


def test_twelve_plain_digits_that_fail_the_checksum_are_not_masked() -> None:
    assert mask_pii("ref 234567890125").counts == {}


@pytest.mark.parametrize("pan", ["ABCPE1234F", "abcpe1234f", "AAACH1234C"])
def test_pan_numbers_are_masked(pan: str) -> None:
    masked = mask_pii(f"PAN: {pan} thanks")
    assert masked.text == f"PAN: {PLACEHOLDERS[PAN]} thanks"


@pytest.mark.parametrize("handle", ["ravi.k@okaxis", "9876543210@ybl", "shop-1@paytm", "a_b@oksbi"])
def test_upi_ids_are_masked(handle: str) -> None:
    masked = mask_pii(f"refund to {handle}.")
    assert masked.text == f"refund to {PLACEHOLDERS[UPI]}."
    assert masked.counts == {UPI: 1}


def test_email_addresses_are_not_mistaken_for_upi_ids() -> None:
    text = "write to asha@example.com or asha.k@mail.co.in"
    assert mask_pii(text) == mask_pii(text)
    assert mask_pii(text).text == text


@pytest.mark.parametrize(
    "text",
    [
        "account number 123456789012",
        "my A/C no: 123456789012",
        "acct 1234 5678 9012 34",
        "Account No. - 000123456789",
    ],
)
def test_bank_account_numbers_are_masked_when_labelled(text: str) -> None:
    masked = mask_pii(text)
    assert PLACEHOLDERS[BANK_ACCOUNT] in masked.text
    assert not any(char.isdigit() for char in masked.text.split(PLACEHOLDERS[BANK_ACCOUNT])[-1])
    assert masked.counts == {BANK_ACCOUNT: 1}


def test_a_labelled_number_that_is_too_short_to_be_an_account_is_kept() -> None:
    text = "my account was charged 5000 twice, order 12345678"
    assert mask_pii(text).text == text


def test_text_before_the_account_number_is_kept() -> None:
    masked = mask_pii("Please refund to account number 123456789012 today")
    assert masked.text == f"Please refund to account number {PLACEHOLDERS[BANK_ACCOUNT]} today"
