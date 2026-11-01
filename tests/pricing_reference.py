"""Independent arbitrary-precision form of SPEC §11.4, with one final USD8 ceil."""

from fractions import Fraction


def integral(
    capacity: int,
    recovery: int,
    work: int,
    q: int,
    base: int,
    surge: int,
    threshold: int,
) -> Fraction:
    start = Fraction(work, capacity * recovery)
    end = Fraction(work + q * recovery, capacity * recovery)
    cutoff = Fraction(threshold, 10_000)
    x0 = max(Fraction(0), (start - cutoff) / (1 - cutoff))
    x1 = max(Fraction(0), (end - cutoff) / (1 - cutoff))
    return (
        Fraction(q * base, 10_000)
        + capacity * (1 - cutoff) * Fraction(surge, 10_000) * (x1**4 - x0**4) / 4
    )


def rounded_fee(*args: int) -> int:
    fee = integral(*args)
    return -(-fee.numerator // fee.denominator)
