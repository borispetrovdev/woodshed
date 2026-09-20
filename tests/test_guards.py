"""The guards on untrusted input must fire, not just let good input through."""
import pytest

from woodshed import library
from woodshed.server import parse_notes


@pytest.mark.parametrize("song_id", ["../escape", "../../etc/passwd", "nested/../../escape"])
def test_song_id_cannot_escape_the_library(song_id: str) -> None:
    with pytest.raises(ValueError):
        library.audio_path(song_id)


def test_plain_song_id_is_accepted() -> None:
    assert library.audio_path("Artist - Title").parent == library.LIBRARY_DIRECTORY


def test_notes_with_unknown_fields_are_rejected() -> None:
    with pytest.raises(TypeError):
        parse_notes({"bogus": 1})


def test_notes_with_malformed_loop_are_rejected() -> None:
    with pytest.raises(ValueError):
        parse_notes({"loop": "everything"})


def test_notes_round_trip_a_loop() -> None:
    notes = parse_notes({"speed": 0.6, "loop": {"start_seconds": 1.0, "end_seconds": 2.0, "enabled": True}})
    assert notes.loop == library.LoopRegion(1.0, 2.0, True)
