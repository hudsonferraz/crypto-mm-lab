"""TickBitmap.nextInitializedTickWithinOneWord over a loaded window of bitmap words."""

from app.models.v3 import TickWindow, WindowExhausted

_WORD_MASK = (1 << 256) - 1


def position(compressed: int) -> tuple[int, int]:
    """(wordPos, bitPos) for a compressed tick; Python >> and & match int16/uint8 casts."""
    return compressed >> 8, compressed & 0xFF


def compress(tick: int, tick_spacing: int) -> int:
    # Solidity: tick / spacing, decremented for negative non-multiples == floor division.
    return tick // tick_spacing


def word_position_for_tick(tick: int, tick_spacing: int) -> int:
    return position(compress(tick, tick_spacing))[0]


def _word(window: TickWindow, word_pos: int) -> int:
    if word_pos < window.min_word or word_pos > window.max_word:
        raise WindowExhausted(f"bitmap word {word_pos} outside loaded window")
    return window.bitmap_words.get(word_pos, 0)


def next_initialized_tick_within_one_word(
    window: TickWindow, tick: int, lte: bool
) -> tuple[int, bool]:
    spacing = window.tick_spacing
    compressed = compress(tick, spacing)

    if lte:
        word_pos, bit_pos = position(compressed)
        mask = (1 << bit_pos) - 1 + (1 << bit_pos)
        masked = _word(window, word_pos) & mask
        initialized = masked != 0
        if initialized:
            next_compressed = compressed - (bit_pos - (masked.bit_length() - 1))
        else:
            next_compressed = compressed - bit_pos
        return next_compressed * spacing, initialized

    word_pos, bit_pos = position(compressed + 1)
    mask = _WORD_MASK ^ ((1 << bit_pos) - 1)
    masked = _word(window, word_pos) & mask
    initialized = masked != 0
    if initialized:
        least_significant = (masked & -masked).bit_length() - 1
        next_compressed = compressed + 1 + (least_significant - bit_pos)
    else:
        next_compressed = compressed + 1 + (255 - bit_pos)
    return next_compressed * spacing, initialized


def initialized_ticks_in_words(bitmap_words: dict[int, int], tick_spacing: int) -> list[int]:
    """Expand bitmap words into the sorted list of initialized ticks they encode."""
    ticks: list[int] = []
    for word_pos in sorted(bitmap_words):
        word = bitmap_words[word_pos]
        while word:
            bit = (word & -word).bit_length() - 1
            ticks.append(((word_pos << 8) + bit) * tick_spacing)
            word &= word - 1
    return ticks
