"""Shared pure CSV quoting validation; historical semantics preserved."""


def validate_quotes(text: str, delimiter: str) -> None:
    quoted = closed = False
    field_start = True
    index = 0
    while index < len(text):
        character = text[index]
        if quoted:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 1
                else:
                    quoted = False
                    closed = True
        elif character in (delimiter, "\r", "\n"):
            field_start = True
            closed = False
        elif character == '"' and field_start and not closed:
            quoted = True
            field_start = False
        elif character == '"' or closed:
            raise ValueError("Malformed CSV quoting.")
        else:
            field_start = False
        index += 1
    if quoted:
        raise ValueError("Malformed CSV quoting.")
