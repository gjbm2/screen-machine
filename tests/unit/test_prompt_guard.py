"""routes.prompt_guard: a refined prompt that drops what the request asked for gets the request's words back."""
from routes.prompt_guard import guard, protected


def test_names_and_sensitive_words_are_protected():
    assert protected("a Gillray cartoon of Keir Starmer and Donald Trump as drunk sailors", ["drunk"]) == \
        ["Donald Trump", "drunk", "Keir Starmer"]
    assert protected("Xi Jinping as Winnie the Pooh") == ["Xi Jinping"]
    assert "naked" in protected("Vladimir Putin naked on horseback")


def test_a_dropped_term_puts_the_request_in_front():
    out, lost = guard("a Modigliani style nude of a woman", "An oil painting of a reclining woman")
    assert lost == ["nude"] and out.startswith("a Modigliani style nude of a woman. An oil painting")


def test_nothing_dropped_leaves_the_prompt_alone():
    assert guard("a misty Scottish loch at dawn", "A misty loch at dawn") == ("A misty loch at dawn", [])
    assert guard("Keir Starmer, nude", "A nude portrait of Keir Starmer")[1] == []
