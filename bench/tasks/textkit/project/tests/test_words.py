from textkit.words import word_counts


def test_word_counts_ignore_case():
    assert word_counts("The cat saw the other cat. THE end.") == {
        "the": 3,
        "cat": 2,
        "saw": 1,
        "other": 1,
        "end": 1,
    }
