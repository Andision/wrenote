from __future__ import annotations

from dataclasses import dataclass

from wrenote.core.upload import merge_whisper_segments


@dataclass
class RawSeg:
    text: str
    t0: int
    t1: int


def test_merge_whisper_segments_preserves_raw_segments() -> None:
    rows = merge_whisper_segments(
        [
            RawSeg(" First short turn.", 0, 250),
            RawSeg(" Second short turn.", 250, 430),
        ]
    )

    assert rows == [
        ("First short turn.", 0.0, 2.5),
        ("Second short turn.", 2.5, 4.3),
    ]


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
