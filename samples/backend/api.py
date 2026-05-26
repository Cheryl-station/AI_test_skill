def add(a, b):
    """Add two non-negative integers."""
    if a < 0 or b < 0:
        raise ValueError("Inputs must be non-negative")
    return a + b


def divide(a, b):
    """Divide a by b."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b
