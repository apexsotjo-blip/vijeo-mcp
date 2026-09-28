"""Lossless tokenizer for Vijeo Designer variable CSV exports.

Vijeo quotes some fields ("Description") and not others, and its importer is
strict, so rows are split into RAW tokens (quotes kept) and re-joined unchanged.
Only the tokens a tool deliberately edits are ever altered.
"""

from __future__ import annotations

ENCODING = "latin-1"      # byte-transparent: every byte round-trips unchanged
EOL = "\r\n"


def split_raw(line: str) -> list[str]:
    """Split on commas outside double quotes, keeping each token verbatim."""
    out, cur, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def join_raw(tokens: list[str]) -> str:
    return ",".join(tokens)


def unquote(token: str) -> str:
    t = token.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].replace('""', '"')
    return t


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_lines(path: str) -> list[str]:
    with open(path, "rb") as f:
        return f.read().decode(ENCODING).split(EOL)


def write_lines(path: str, lines: list[str]) -> None:
    with open(path, "wb") as f:
        f.write(EOL.join(lines).encode(ENCODING))
