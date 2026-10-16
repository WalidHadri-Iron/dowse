from dowse import searchtext


def test_normalized_folds_arabic_and_drops_joiners():
    assert searchtext.normalized("أحمد") == "احمد"
    assert searchtext.normalized("مُدَرِّسَة") == "مدرسة"
    assert searchtext.normalized("ক্‍ষ") == "ক্ষ"
    assert searchtext.normalized("ｆｕｌｌ") == "full"


def test_terms_drop_stopwords_and_kind_words():
    assert searchtext.terms("a screenshot of the payment declined error") == ["payment", "declined", "error"]
    assert searchtext.terms("Error error ERROR") == ["error"]
    assert searchtext.terms("ইলিশ মাছ") == ["ইলিশ", "মাছ"]


def test_patterns_prefix_inflecting_scripts_and_the_typed_word():
    assert searchtext.fts_patterns(["cat", "dog"], prefix_last=True) == ['"cat"', '"dog"*']
    assert searchtext.fts_patterns(["মাছ"]) == ['"মাছ"*']
    assert searchtext.fts_patterns(['a"b']) == ['"a""b"']


def test_has_words():
    assert searchtext.has_words("The rent is 1,200 euros per month.")
    assert not searchtext.has_words("1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17")
    assert searchtext.has_words("আগামী শুক্রবার বাজার")


def test_chunks_overlap_and_cut_at_spaces():
    text = " ".join(f"word{i}" for i in range(1000))
    pieces = searchtext.chunks(text, size=200, overlap=40)
    assert all(len(p) <= 200 for p in pieces)
    assert all(not p.startswith(" ") and not p.endswith(" ") for p in pieces)
    assert pieces[0].split()[-1] in pieces[1]     # they overlap
    assert pieces[-1].endswith("word999")
    assert searchtext.chunks("") == []


def test_html_to_text():
    html = "<html><head><title>x</title></head><body><script>var a</script><p>One &amp; two</p><p>three</p></body>"
    assert searchtext.html_to_text(html) == "One & two\nthree"


def test_snippet_marks_words_as_written():
    text = "Any damage caused by Pets will be deducted from the security deposit."
    assert searchtext.snippet(text, ["pets"]) == "Any damage caused by «Pets» will be deducted from the security deposit."
    assert searchtext.snippet(text, ["unicorn"]) is None
    assert searchtext.snippet("কুমিরের গল্প", ["কুমির"]) == "«কুমিরের» গল্প"
