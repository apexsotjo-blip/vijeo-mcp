"""File-safety rules shared by every tool that writes.

The tool never modifies a Vijeo project (.vdz) and never overwrites the file it
read from: every edit produces a NEW file that the designer imports through
Vijeo Designer, which validates it.
"""

from __future__ import annotations

import os


class SafetyError(RuntimeError):
    """A write was refused to protect the designer's files."""


def check_output(out_path: str, *inputs: str, overwrite: bool = False, allow_vdz: bool = False) -> str:
    """allow_vdz: only the graphics save writes a .vdz - always a NEW file under a NEW project name."""
    out = os.path.abspath(out_path)
    if out.lower().endswith(".vdz") and not allow_vdz:
        raise SafetyError("Refusing to write a .vdz here: only save_edited_project writes (new) .vdz files.")
    for src in inputs:
        if src and os.path.abspath(src).lower() == out.lower():
            raise SafetyError(f"Refusing to overwrite the input file {src}. Choose a new output path.")
    if os.path.exists(out) and not overwrite:
        raise SafetyError(f"{out} already exists. Pass overwrite=True to replace it.")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    return out
