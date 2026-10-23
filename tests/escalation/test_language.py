import pytest

from services.api.escalation.language import is_non_english


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        ("ऑर्डर कहाँ है", "मेरा ऑर्डर RD-10001 अभी तक नहीं पहुँचा। कृपया बताइए कि वह कहाँ है।"),  # Hindi
        ("ऑर्डर", "माझी ऑर्डर अजून आलेली नाही. कृपया सांगा काय झाले?"),  # Marathi
        (None, "என் ஆர்டர் இன்னும் வரவில்லை, எங்கே இருக்கிறது?"),  # Tamil
        ("طلبي", "لم يصل طلبي بعد، أين هو؟"),  # Arabic
        (None, "Где мой заказ? Он не пришёл."),  # Russian
        ("订单", "我的订单还没有到,请告诉我在哪里"),  # Chinese
    ],
)
def test_a_message_in_another_script_escalates(subject: str | None, body: str) -> None:
    assert is_non_english(subject, body)


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        (None, "Where is my order RD-10001? It was due on Monday."),
        ("Café order", "Hi, my crème brûlée torch from order RD-10002 is broken. Señor Ávila"),
        ("Order", "Hello नमस्ते, where is my order? It has not arrived and I paid ₹2,499."),
        ("🙏", "Please help 😡 my order is late!!! ₹999"),
        (None, "1234 5678 -- ??"),
        (None, ""),
    ],
)
def test_english_with_accents_symbols_or_a_borrowed_word_goes_on(
    subject: str | None, body: str
) -> None:
    assert not is_non_english(subject, body)


def test_another_language_in_latin_letters_is_not_caught() -> None:
    """The documented limit: it needs a language model, which this check is not."""
    assert not is_non_english(None, "mera order abhi tak nahi aaya, kripya batayein")
