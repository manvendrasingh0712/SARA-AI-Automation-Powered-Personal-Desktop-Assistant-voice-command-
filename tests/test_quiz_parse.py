"""Live-test finding: "quiz me on milk" -> "I couldn't make a quiz": the model's Q/A pairs arrive on one line."""
from sara.skills.notes_qa import parse_quiz

ONE_LINE = ("Q1: What is a key nutrient in milk? A1: Calcium and protein. Q2: Which type of milk has the least fat? A2: Skimmed milk. "
            "Q3: What is the fat content of full cream milk? A3: About 3.5 percent.")
MULTI = "Q1: What is milk?\nA1: A dairy drink.\nQ2: Why boil it?\nA2: To make it safe.\nQ3: Where to store it?\nA3: In the fridge."


def test_one_line_reply_gives_three_pairs():
    qa = parse_quiz(ONE_LINE)
    assert len(qa) == 3
    assert qa[0] == ("What is a key nutrient in milk?", "Calcium and protein.")
    assert qa[2][1] == "About 3.5 percent."


def test_the_old_multiline_format_still_parses():
    qa = parse_quiz(MULTI)
    assert [q for q, _ in qa] == ["What is milk?", "Why boil it?", "Where to store it?"]
    assert qa[1][1] == "To make it safe."


def test_other_marker_styles_and_junk():
    assert len(parse_quiz("Q1) One? A1) Uno. Q2) Two? A2) Dos.")) == 2
    assert parse_quiz("") == [] and parse_quiz(None) == [] and parse_quiz("no quiz here") == []
    assert len(parse_quiz("Sure! Q1: A? A1: B. Q2: C? A2: D. Hope that helps")) == 2
