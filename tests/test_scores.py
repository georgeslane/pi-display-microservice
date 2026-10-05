"""Photo scores: the weighting, and keeping count of likes and dislikes."""

import json
import logging

import pytest

from pi_display_microservice.scores import Scores, weight


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0, 1.0),  # every photo starts at 1
        (1, 1.5),  # 2 - 1/(1+x)
        (3, 1.75),
        (9, 1.9),
        (-1, 0.5),  # 1/(1+|x|)
        (-3, 0.25),
        (-9, 0.1),
    ],
)
def test_the_weighting(x, expected):
    assert weight(x) == pytest.approx(expected)


def test_likes_raise_the_weight_towards_2_and_dislikes_lower_it_towards_0():
    weights = [weight(x) for x in range(-1000, 1001)]
    assert weights == sorted(weights) and len(set(weights)) == len(weights)  # every like counts
    assert 0 < weights[0] < 0.01 and 1.99 < weights[-1] < 2


def test_counts_likes_and_dislikes_for_each_photo(tmp_path):
    scores = Scores(tmp_path / "scores.json")
    assert scores.score("a") == 0 and scores.weight("a") == 1.0
    assert [scores.like("a"), scores.like("a"), scores.dislike("a"), scores.like("a")] == [1, 2, 1, 2]
    assert [scores.dislike("b"), scores.dislike("b")] == [-1, -2]
    assert (scores.score("a"), scores.weight("a")) == (2, pytest.approx(5 / 3))
    assert (scores.score("b"), scores.weight("b")) == (-2, pytest.approx(1 / 3))
    assert scores.score("c") == 0


def test_scores_are_remembered_after_a_restart(tmp_path):
    path = tmp_path / "board" / "scores.json"  # the folder is made if need be
    first = Scores(path)
    first.like("a")
    first.dislike("b")
    again = Scores(path)
    assert (again.score("a"), again.score("b")) == (1, -1)
    assert json.loads(path.read_text()) == {"a": {"dislikes": 0, "likes": 1}, "b": {"dislikes": 1, "likes": 0}}
    assert sorted(p.name for p in path.parent.iterdir()) == ["scores.json"]  # no half-written copies left behind


@pytest.mark.parametrize(
    "text", ["not json", '["a list"]', '{"a": {"likes": -1}}', '{"a": {"likes": true}}', '{"a": 3}']
)
def test_a_damaged_file_is_kept_aside_and_counting_starts_again(tmp_path, caplog, text):
    path = tmp_path / "scores.json"
    path.write_text(text)
    with caplog.at_level(logging.WARNING):
        scores = Scores(path)
    assert scores.score("a") == 0
    assert (tmp_path / "scores.json.broken").read_text() == text  # not lost, for a person to look at
    assert "Starting the photo scores again" in caplog.text
    scores.like("a")
    assert Scores(path).score("a") == 1


def test_a_disk_it_cannot_write_to_does_not_stop_the_slideshow(tmp_path, caplog):
    (tmp_path / "a-file").write_text("")
    scores = Scores(tmp_path / "a-file" / "scores.json")  # can't make a folder where a file is
    with caplog.at_level(logging.ERROR):
        assert scores.like("a") == 1
    assert scores.score("a") == 1  # still counted, until a restart
    assert "Couldn't save the photo scores" in caplog.text
