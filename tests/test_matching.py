"""
TMDB title matching: scoring, the year and popularity nudges, and the
near-tie ("ambiguous") detection.

best_match() never calls the network; it just ranks candidate dicts. The cases
below are the real collisions the scoring rules were written for.
"""
import enrich_tmdb as m


def tv(id_, name, first_air_date="", popularity=1.0):
    return {"id": id_, "name": name, "first_air_date": first_air_date, "popularity": popularity}


def movie(id_, title, release_date="", popularity=1.0):
    return {"id": id_, "title": title, "release_date": release_date, "popularity": popularity}


def test_normalize_and_similarity_ignore_case_and_punctuation():
    assert m.normalize("The  Show: Part-1!") == "the show part 1"
    assert m.similarity("Some Show", "some.show") == 1.0
    assert m.similarity("abc", "xyz") < 0.2


def test_no_results_or_nothing_close_is_no_match():
    assert m.best_match([], "Anything", None, "release_date") is None
    assert m.best_match([], "Anything", None, "release_date", return_ambiguous=True) == (None, None)
    far = [movie(1, "Completely Different Words Here", "2000-01-01")]
    assert m.best_match(far, "Zebra", 2000, "release_date") is None


def test_year_in_filename_picks_the_right_remake():
    results = [movie(1, "Night Ferry", "1974-05-01"), movie(2, "Night Ferry", "2019-05-01")]
    assert m.best_match(results, "Night Ferry", 2019, "release_date")["id"] == 2
    assert m.best_match(results, "Night Ferry", 1974, "release_date")["id"] == 1


def test_year_a_decade_off_is_penalised():
    results = [movie(1, "Harbor Nine", "1990-01-01", 5), movie(2, "Harbor Nine", "2021-01-01", 5)]
    assert m.best_match(results, "Harbor Nine", 2022, "release_date")["id"] == 2


def test_string_year_from_a_bad_record_does_not_crash():
    results = [movie(1, "Moth Season", "2021-01-01")]
    assert m.best_match(results, "Moth Season", "2021", "release_date")["id"] == 1
    assert m.best_match(results, "Moth Season", "not a year", "release_date")["id"] == 1


def test_popularity_breaks_a_close_spelling_call():
    # The real case: the query "X US" is closer, string for string, to an obscure
    # near-namesake than to the show everyone means. Popularity decides it.
    decoy = tv(1, "Cooking Show USA", "2000-01-01", popularity=2.6)
    real = tv(2, "Cooking Show US", "2010-01-01", popularity=32)
    assert m.best_match([decoy, real], "Cooking Show U.S.", None, "first_air_date")["id"] == 2


def test_popularity_cannot_beat_a_real_title_match():
    exact = tv(1, "Kestrel and Vane", "2026-01-01", popularity=0.5)
    famous_but_unrelated = tv(2, "Completely Other Programme", "2020-01-01", popularity=5000)
    assert m.best_match([famous_but_unrelated, exact], "Kestrel and Vane", None, "first_air_date")["id"] == 1


def test_near_tie_between_two_exact_titles_is_reported_not_hidden(capsys):
    original = tv(11294, "Hotel Rescue", "2007-09-19", popularity=10)
    reboot = tv(235884, "Hotel Rescue", "2023-05-01", popularity=10)
    top, runner = m.best_match([original, reboot], "Hotel Rescue", None, "first_air_date", return_ambiguous=True)
    assert top["id"] in (11294, 235884) and runner["id"] in (11294, 235884) and top["id"] != runner["id"]
    assert "AMBIGUOUS MATCH" in capsys.readouterr().out


def test_a_year_in_the_filename_resolves_the_tie():
    original = tv(1, "Hotel Rescue", "2007-09-19", popularity=10)
    reboot = tv(2, "Hotel Rescue", "2023-05-01", popularity=10)
    top, runner = m.best_match([original, reboot], "Hotel Rescue", 2023, "first_air_date", return_ambiguous=True)
    assert top["id"] == 2 and runner is None


def test_two_merely_similar_titles_are_not_called_ambiguous():
    a = tv(1, "The Salt Lighthouse", "2022-01-01", popularity=5)
    b = tv(2, "The Salt Lighthouse Keepers Diary Special Edition Extra", "2022-01-01", popularity=5)
    top, runner = m.best_match([a, b], "The Salt Lighthouse", None, "first_air_date", return_ambiguous=True)
    assert top["id"] == 1 and runner is None


def test_same_id_listed_twice_is_not_a_tie_with_itself():
    a = tv(7, "Low Tide Radio", "2024-01-01")
    top, runner = m.best_match([a, dict(a)], "Low Tide Radio", None, "first_air_date", return_ambiguous=True)
    assert top["id"] == 7 and runner is None


def test_default_call_still_returns_just_the_pick():
    results = [movie(1, "Second Summit", "2019-01-01")]
    assert m.best_match(results, "Second Summit", 2019, "release_date")["id"] == 1


def test_hash_like_titles_are_recognised():
    assert m.looks_like_hash("a3f9c2d1e8b7a6f5" * 2)
    assert not m.looks_like_hash("Brightwater Academy")
