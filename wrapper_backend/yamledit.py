"""Comment-preserving, line-based edits of the hand-written vision config.

The ``vision_processor`` configs are commented by hand, so they are never
round-tripped through PyYAML (``yaml.safe_dump`` would drop every comment).
Instead, single keys inside one top-level section are rewritten line by
line and everything else stays byte-identical. Callers re-parse the result
with ``yaml.safe_load`` and only then write it with :func:`atomic_write`.

Supported layouts (all that the repo's configs use):

- ``  key: <inline value>  # comment`` (scalar or flow list; the comment is
  kept),
- ``  key:`` followed by a block sequence (``  - [1, 2]`` at the same or a
  deeper indent) or a deeper-indented block,
- a commented-out placeholder ``  #key:`` followed by commented ``  #  - ...``
  items, which is removed when listed in ``drop_commented`` (the new key is
  written where the placeholder was).
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

_TOP_LEVEL_RE = re.compile(r"^[^\s#]")
_INDENT_RE = re.compile(r"^(\s+)\S")
# `  name: [1, 2, 3]   # comment` -> indent, key, rest-after-colon.
_KEY_RE = re.compile(r"^(?P<indent>\s+)(?P<key>[A-Za-z_][\w-]*)\s*:(?P<rest>.*)$")
_COMMENTED_ITEM_RE = re.compile(r"^\s*#\s*-\s")

# Inline text (scalar or flow collection), or the items of a block sequence.
Value = str | list[str]


class YamlEditError(Exception):
    """The config file's layout can't be edited safely."""


def _section_re(section: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")


def _trailing_comment(rest: str) -> str:
    """Return the ``  # ...`` suffix of an inline value, if any."""
    stripped = rest.lstrip()
    if stripped.startswith("["):
        # Matching bracket of the (possibly nested) flow list.
        depth = 0
        end = -1
        for pos, char in enumerate(rest):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        if end < 0:
            return ""
        after = rest[end + 1 :]
        hash_at = after.find("#")
        if hash_at < 0 or after[:hash_at].strip():
            return ""
        return after.rstrip("\n")
    # Plain scalar (number/bool): a comment starts at the first " #".
    match = re.search(r"\s#", rest)
    if match is None or stripped.startswith(("'", '"')):
        return ""
    return rest[match.start() :].rstrip("\n")


def _render(indent: str, key: str, value: Value, comment: str = "") -> list[str]:
    if isinstance(value, str):
        return [f"{indent}{key}: {value}{comment}\n"]
    return [f"{indent}{key}:{comment}\n"] + [f"{indent}  - {item}\n" for item in value]


def _skip_block_value(lines: list[str], i: int, end: int, indent: str) -> int:
    """Index after a key's block-style value starting at ``lines[i]``.

    The value is the following deeper-indented lines, or a block sequence
    (``- item``) at the key's own indentation.
    """
    while i < end:
        child = _INDENT_RE.match(lines[i])
        if child is None:
            break
        deeper = len(child[1]) > len(indent)
        same_level_item = child[1] == indent and lines[i][len(indent) :].startswith(
            "- "
        )
        if not (deeper or same_level_item):
            break
        i += 1
    return i


def update_section(
    text: str,
    section: str,
    values: Mapping[str, Value],
    *,
    drop_commented: Iterable[str] = (),
    remove: Iterable[str] = (),
    require_flow_list: bool = False,
) -> str:
    """Set ``values`` inside the top-level ``section:`` mapping of ``text``.

    Existing ``<key>:`` lines are replaced in place (keeping a trailing
    comment on inline values); a commented-out ``#<key>:`` placeholder block
    for keys in ``drop_commented`` is removed and the key written in its
    place; remaining keys are appended after the section's last indented
    line; a missing section is appended at the end of the file. Everything
    else — comments, blank lines, other sections — is left byte-identical.

    Keys in ``remove`` are deleted (with a block-style value).
    ``require_flow_list`` refuses to replace an existing inline value that
    is not a flow list (used by the colour editor).
    """
    lines = text.splitlines(keepends=True)
    section_re = _section_re(section)
    start = next(
        (i for i, line in enumerate(lines) if section_re.match(line.rstrip("\n"))),
        None,
    )

    if start is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        block = [f"{section}:\n"]
        for key, value in values.items():
            block += _render("  ", key, value)
        return text + suffix + "".join(block)

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _TOP_LEVEL_RE.match(lines[i]):
            end = i
            break

    # Child indentation: taken from the first indented key in the section.
    indent = "  "
    for i in range(start + 1, end):
        match = _KEY_RE.match(lines[i].rstrip("\n"))
        if match:
            indent = match["indent"]
            break

    placeholder_res = {
        key: re.compile(rf"^{re.escape(indent)}#\s*{re.escape(key)}\s*:")
        for key in drop_commented
    }

    pending = dict(values)
    removing = set(remove) - set(values)
    out = lines[: start + 1]
    placeholder_at: int | None = None
    i = start + 1
    while i < end:
        line = lines[i]
        bare = line.rstrip("\n")

        placeholder = next(
            (key for key, regex in placeholder_res.items() if regex.match(bare)),
            None,
        )
        if placeholder is not None:
            # Drop `#key:` and its commented `#  - item` lines.
            i += 1
            while i < end and _COMMENTED_ITEM_RE.match(lines[i]):
                i += 1
            if placeholder_at is None:
                placeholder_at = len(out)
            continue

        match = _KEY_RE.match(bare)
        if match and match["indent"] == indent and match["key"] in removing:
            i = _skip_block_value(lines, i + 1, end, indent)
            continue
        if match and match["indent"] == indent and match["key"] in pending:
            key = match["key"]
            rest = match["rest"]
            comment = ""
            stripped = rest.strip()
            if stripped and not stripped.startswith("#"):
                if require_flow_list and not stripped.startswith("["):
                    raise YamlEditError(
                        f"{section}.{key} is not a flow list; edit it by hand"
                    )
                comment = _trailing_comment(rest)
            elif stripped.startswith("#"):
                comment = " " + stripped
            rendered = _render(indent, key, pending.pop(key), comment)
            if not line.endswith("\n"):
                rendered[-1] = rendered[-1].rstrip("\n")
            out += rendered
            i = _skip_block_value(lines, i + 1, end, indent)
            continue
        out.append(line)
        i += 1

    if pending:
        if placeholder_at is not None:
            insert_at = placeholder_at
        else:
            # After the last indented (non-blank) line of the section, so
            # blank lines separating it from the next section stay put.
            insert_at = start + 1
            for j in range(len(out) - 1, start, -1):
                if out[j].strip():
                    insert_at = j + 1
                    break
        if insert_at > 0 and not out[insert_at - 1].endswith("\n"):
            out[insert_at - 1] += "\n"
        added: list[str] = []
        for key, value in pending.items():
            added += _render(indent, key, value)
        out[insert_at:insert_at] = added

    return "".join(out) + "".join(lines[end:])


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` via a temp file in the same dir + ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        except FileNotFoundError:
            pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
