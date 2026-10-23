import pytest

from services.api.grounding.sign_off import with_sign_off

SIGN_OFF = "Kaveri Home Goods Support"


@pytest.mark.parametrize(
    "text",
    [
        "Hello Asha.\n\nYour order is on its way.",
        f"Hello Asha.\n\nYour order is on its way.\n\n{SIGN_OFF}",
        f"Hello Asha.\n\nYour order is on its way.\n\n{SIGN_OFF}\n\n",
        "Hello Asha.\n\nYour order is on its way.\n\nBest regards,\nKaveri Home Goods Support",
        "Hello Asha.\n\nYour order is on its way.\n\nWarm regards,\n\nKaveri Home Goods Team",
        f"Hello Asha.\n\nYour order is on its way.\n\nThanks,\n{SIGN_OFF}.",
        "Hello Asha.\n\nYour order is on its way.\n\nKaveri Home Goods Support Team",
        "Hello Asha.\n\nYour order is on its way.\n\nThank you.",
        "Hello Asha.\n\nYour order is on its way.\nSincerely,\nThe Kaveri Team",
    ],
)
def test_the_reply_ends_with_the_exact_sign_off_once(text: str) -> None:
    assert (
        with_sign_off(text, SIGN_OFF) == f"Hello Asha.\n\nYour order is on its way.\n\n{SIGN_OFF}"
    )


def test_a_real_last_sentence_is_kept() -> None:
    text = "Hello Asha.\n\nThank you for contacting us.\n\nKaveri Home Goods will review it."
    fixed = with_sign_off(text, SIGN_OFF)
    assert "Thank you for contacting us." in fixed
    assert "Kaveri Home Goods will review it." in fixed
    assert fixed.endswith(f"\n\n{SIGN_OFF}")


def test_the_sign_off_is_added_when_the_model_wrote_only_a_closing() -> None:
    assert with_sign_off("Regards,", SIGN_OFF) == SIGN_OFF
