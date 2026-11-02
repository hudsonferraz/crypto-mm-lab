"""FullMath: 512-bit-safe mulDiv. Python ints are unbounded, so floor/ceil are exact."""


def mul_div(a: int, b: int, denominator: int) -> int:
    if denominator <= 0:
        raise ZeroDivisionError("mul_div denominator must be positive")
    return (a * b) // denominator


def mul_div_rounding_up(a: int, b: int, denominator: int) -> int:
    if denominator <= 0:
        raise ZeroDivisionError("mul_div denominator must be positive")
    product = a * b
    result = product // denominator
    if product % denominator:
        result += 1
    return result


def div_rounding_up(x: int, y: int) -> int:
    if y <= 0:
        raise ZeroDivisionError("div_rounding_up divisor must be positive")
    return -(-x // y)
