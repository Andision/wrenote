from __future__ import annotations

from dataclasses import dataclass

from wrenote.core.upload import merge_whisper_segments


@dataclass
class RawSeg:
    text: str
    t0: int
    t1: int


def test_short_utterances_join_into_one_row() -> None:
    """Whisper emits a segment per utterance, and in a meeting most
    utterances are backchannel. A 28-minute recording came out as 830 rows
    with a median of 16 characters — "Yeah.", "okay", "we" — because nothing
    put them back together."""
    rows = merge_whisper_segments(
        [
            RawSeg(" First short turn.", 0, 250),
            RawSeg(" Second short turn.", 250, 430),
        ]
    )

    assert rows == [("First short turn. Second short turn.", 0.0, 4.3)]


def test_a_finished_sentence_of_real_length_ends_the_row() -> None:
    rows = merge_whisper_segments(
        [
            RawSeg(" So the request goes to the edge first, and then to the node.", 0, 500),
            RawSeg(" That is where the inference runs.", 500, 800),
        ]
    )

    assert [r[0] for r in rows] == [
        "So the request goes to the edge first, and then to the node.",
        "That is where the inference runs.",
    ]


def test_a_speaker_who_never_pauses_still_gets_rows() -> None:
    """The caps are what stop one unbroken monologue becoming one row —
    clicking a line should not jump you half a minute back."""
    long_run = [RawSeg(" and then we tried the other node as well", i * 300, (i + 1) * 300)
                for i in range(12)]
    rows = merge_whisper_segments(long_run)

    assert len(rows) > 1
    assert all(r[2] - r[1] <= 27.0 for r in rows)
    assert all(len(r[0]) <= 260 for r in rows)


def test_rows_never_merge_across_a_change_of_speaker() -> None:
    """A "-A -B" split is two people; joining them would attribute one's
    words to the other."""
    rows = merge_whisper_segments(
        [RawSeg("-Short one. -Short two. -Short three.", 0, 900)]
    )

    assert [r[0] for r in rows] == ["Short one.", "Short two.", "Short three."]


def test_merge_whisper_segments_splits_subtitle_style_dialogue_turns() -> None:
    rows = merge_whisper_segments(
        [
            RawSeg(
                '-Who does not love Anne Hathaway? Thank you for coming back to -- '
                '-That is very nice. -Yeah, there you go.',
                0,
                900,
            )
        ]
    )

    assert [row[0] for row in rows] == [
        "Who does not love Anne Hathaway? Thank you for coming back to --",
        "That is very nice.",
        "Yeah, there you go.",
    ]
    assert rows[0][1] == 0.0
    assert rows[-1][2] == 9.0


def test_merge_whisper_segments_does_not_split_em_dash_like_double_hyphen() -> None:
    rows = merge_whisper_segments(
        [RawSeg('And then the last -- I am like, "the points are on."', 0, 500)]
    )

    assert rows == [
        ('And then the last -- I am like, "the points are on."', 0.0, 5.0)
    ]
