"""routes.prompt_guard: a refined prompt that drops what the request asked for gets the request's words back."""
from routes.prompt_guard import guard, protected, trim_figure


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


def test_a_figure_prompt_keeps_its_first_two_sentences_and_is_cut_to_whole_sentences():
    first = "A photograph of a man and a woman having sex. " + "Seen from the foot of the bed: " + "word " * 80 + "end."
    out = trim_figure(first + " The room is grey. The light is soft and low. " + "Palette of greys. " * 10)
    assert out.startswith(first.strip()) and out.endswith("end.")      # 2 sentences over the limit: both kept, no more
    short = trim_figure("A nude. Seen from the front, standing. The room is grey. The light is soft.")
    assert short == "A nude. Seen from the front, standing. The room is grey. The light is soft."
