"""Nothing in this repo points a reader at an issue or a pull request they
cannot open.

This example ships without its issue tracker, so an issue or pull request
number points a reader at a page they cannot open, whichever repository it
names, and cites a reason they cannot read. Every rule in this repo has to
stand on its own words instead.

This check reads every tracked file at once, and it runs in the same
`pytest -q` as every other test. Four rules shape it.

**Case is not a defence**, and neither is punctuation: a reference with the
hyphen dropped, with a space in its place, with a non-breaking hyphen, or
written out in words, names exactly what the canonical spelling names. Every
pattern here is case-insensitive and reads those variants.

**A PDF is not a binary input.** The two documents this example works from
carry text a reader extracts in one line, so they are read as text like
everything else rather than skipped. Nothing else is skipped either: a file
this module cannot decode as UTF-8 is read as bytes, because a binary can
carry text a reader pulls out as surely as a PDF does.

**What a reference is written with is normalized before it is read.**
Unicode has a dozen hyphens and dashes, a minus sign, a soft hyphen and a
zero-width space that print as a hyphen or as nothing, and a fullwidth hash
that prints as a hash; each of them can spell a reference the patterns
would not otherwise read as one. `forbidden_in` folds them all first, and
every check here goes through it.

**This file does not exempt itself.** It is tracked and published like any
other file, and a file the guard cannot see is where a reference lands
unseen. So every reference in a fixture below is assembled from parts, every
number in one is plainly not a real one, and every repository and person in
one is a made-up stand-in. This file is scanned like any other.

What survives: a repository or a package named with no number after it
(`sanning-anchor`, `sanning-proof`), and public endpoints. A number does
not, in this repository or in any other: no issue tracker ships with this
example.
"""

from __future__ import annotations

import functools
import hashlib
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from credit_demo.common.documents import REPO_ROOT

# Every literal this file would otherwise be the first finding of, assembled
# from parts. Each pattern's name says what it is looking for, in words a
# reader of a failure can act on.
_HASH = "#"
# Every separator a term is written with in practice: the ASCII hyphen, a
# non-breaking hyphen (U+2011), an en dash, an underscore, any run of
# whitespace, or nothing at all. One class, used everywhere a term has a
# hyphen or a space in it, because a guard that knows the underscored
# spelling of one term and not of the next is a guard whose coverage depends
# on which line somebody edited last.
#
# It is a run (`*`) rather than one character. Two spaces before a number,
# and a reference a paragraph broke across two lines, are both a reference,
# and a class that allowed exactly one separator would miss both.
_SEP = "[-_\u2011\u2013\\s]*"
# The same separators between a term and its number, and the slash a path
# puts there.
_GAP = "[-_/\u2011\u2013\\s]*"

# Where a term starts. Not `\b`: Python counts `_` as a word character, so
# `\b` finds no boundary between an underscore and a letter, and a file name
# with an issue number after an underscore would pass a guard that catches
# the same number after a hyphen. A term starts after anything that is not a
# letter or a digit, the underscore included.
_START = "(?<![A-Za-z0-9])"

# What a numbered reference is called: an issue, a ticket, and a pull request
# by either name. A plural is also a verb, as in a sentence where the agent
# pulls two documents or a service issues three requests, with digits. So a
# plural with no hash counts as a reference only when its number could not
# be a count: two digits or more, or a number that ends the clause or starts
# a list. The singular `pull` is a verb in prose, so it counts only as a
# segment of a link's path.
_ONE = r"(issue|ticket|PR|pull" + _SEP + r"request)"
_MANY = r"(issues|tickets|PRs|pull" + _SEP + r"requests|pulls)"
# A number as a reference is written: after a hash, bare, or after `no.`,
# `nr.` or `number`.
_NO = r"(?:(?:no\.?|nr\.?|num(?:ber)?\.?)\s*)?"
# A number written out in words is the same reference. Only after the
# singular, where a word cannot be a count of the things the plural names.
_NUMBER_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)(?![A-Za-z])"
)
_NUMBER_AFTER_ONE = _GAP + _NO + r"(?:" + _HASH + r"*\s*\d+|" + _NUMBER_WORD + r")"
# A single digit counts after a plural when a range or a list follows it
# (`issues N to M`, `pulls N through M`, `issues N and M`).
_NUMBER_AFTER_MANY = (
    _GAP
    + _NO
    + r"(?:"
    + _HASH
    + r"+\s*\d+|\d{2,}|\d(?=\s*(?:$|[,.;:)\]]|and\b|or\b|to\b|through\b|thru\b|till\b|until\b|-)))"
)
_NAMED_THING = r"(?:" + _ONE + _NUMBER_AFTER_ONE + r"|" + _MANY + _NUMBER_AFTER_MANY + r")"
# Markup a name is written inside: backticks, bold, italics.
_MARKUP = r"[`*_~]*"
# A hash and digits is not always an issue number. After `&` it is a
# character reference, and it is a colour when it ends a CSS declaration
# (`;` or `}` straight after it) or has six or eight digits, the lengths a
# colour comes in. An issue number that long passes: the stated limit, for a
# guard that would otherwise flag every stylesheet. A number that runs on
# into a letter, a decimal or a percent sign is a colour or a quantity.
_NOT_A_COLOUR = r"(?!(?:\d{6}|\d{8})(?![0-9]))"
_NUMBER_END = r"(?![0-9A-Za-z%]|\.\d|\s*[;}])"

FORBIDDEN = {
    "an issue or pull request by its number": re.compile(
        _START + _NAMED_THING, re.IGNORECASE
    ),
    # A hash and a number, with a repository's name before it or with none:
    # the name changes nothing, since no repository's issues ship with this
    # example.
    "an issue or pull request by a hash and its number": re.compile(
        r"(?<!&)" + _HASH + r"+" + _NOT_A_COLOUR + r"\d+" + _NUMBER_END
    ),
    # A hash with a space after it, which is also how a comment opens. So it
    # counts only after a word (a name, in markup or not), with at most one
    # space before the hash: two spaces and a hash is how an inline comment
    # opens. The stated limit: a comment line that opens with a number, under
    # a line that ends with a word, is read joined to it (`_windows`) and is a
    # finding; open the line with a word instead.
    "an issue by its number, with the hash set apart from it": re.compile(
        r"[A-Za-z]"
        + _MARKUP
        + r"[:,(\[]*\s?"
        + _HASH
        + r"+\s+"
        + _NOT_A_COLOUR
        + r"\d+"
        + _NUMBER_END
    ),
    # A link to one, on any host and at any depth: the issue or pull request
    # segment of the path and the number after it.
    "a link to an issue or pull request": re.compile(
        r"/(?:issues|pulls?)/+\d+", re.IGNORECASE
    ),
}

# People, by name. This file is scanned and published, so it cannot carry
# the names, even assembled from parts: what it holds is the first 64 bits of the SHA-256 of
# each name's SKELETON (`_skeleton`), and every word of every line is reduced
# to its skeleton, hashed and looked up.
#
# THE RULE:
#
# - Found anywhere, in any case, in any word of any line, identifier, email,
#   commit trailer or file name: every full name and surname on the list,
#   every handle, and every first name that is not an ordinary English word.
# - The first names on the list that ARE ordinary English words are found
#   only where the word is a name by its context: beside a surname or a
#   handle, after a role word (`_ROLE`), inside `TODO(...)`, or as part of an
#   email address's local part. The stated limit: the word alone ("<name>
#   reviewed it", "sent to <name>") is NOT caught, because the same word is
#   ordinary English, and a rule on title case, capitals, questions or
#   sentence starts that tries to tell the two apart lets a name through or
#   flags ordinary English.
_PEOPLE = frozenset({
    "1a12dd37d49ace0f",
    "a524566c1f3e4e56",
    "438731e9e544ae16",
    "d0cfc2e5319b82cd",
    "8dd5c9d8bbdb8ac7",
    "4c4591ddc74535e3",
    "e9625ce02e276f19",
    "e46a9e3016a13c70",
    "e19efe9da830d98d",
    "53aab1ecf7c00e75",
    "ca9ce19529856a4c",
})
# The surnames and handles among them, which make an English-word first name
# beside them a name.
_SURNAMES_AND_HANDLES = frozenset({
    "4c4591ddc74535e3",
    "e9625ce02e276f19",
    "e46a9e3016a13c70",
    "e19efe9da830d98d",
    "53aab1ecf7c00e75",
    "ca9ce19529856a4c",
})
# The first names that are also ordinary English words.
_WORD_NAMES = frozenset({"0dd82a1381c4f9bc", "3faa8e9608f73800", "da2acb87a673c5bc"})
# A word, as a name is spelled in one: letters and digits, since a digit is
# how a letter is faked (`_SKELETON`).
_WORD = re.compile(r"[A-Za-z0-9]+")
# The words a joined name is made of: `FirstnameSurname` is two words, and
# each is hashed.
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
# A role word right before a name, with what sits between them in a byline,
# a trailer, a table cell, a key and value, or markup.
_ROLE = re.compile(
    r"(?<![A-Za-z0-9])(?:reviewers?|owners?|approvers?|cc|from|by|for)"
    r"[\s:=*_\"'`(<\[|>-]*$",
    re.IGNORECASE,
)
_TODO_OPEN = re.compile(r"(?<![A-Za-z0-9])todo\(\s*$", re.IGNORECASE)
_TODO_CLOSE = re.compile(r"^\s*\)")
# An email address's local part, which a name in any case is a name inside.
_LOCAL_PART = re.compile(r"[A-Za-z0-9._+-]+(?=@[A-Za-z0-9-]+\.)")
# What may sit between a first name and the surname beside it.
_BESIDE = re.compile(r"^[\s._,'-]{1,3}$")

# The letters a name is faked with, read as the letter they pass for: the
# i/l/1/| family all as `l` (a capital I is an l in most sans-serif faces),
# and the digits that stand in for letters. A skeleton is for comparing, so
# folding `i` and `l` together costs nothing: both sides are folded alike.
_SKELETON = str.maketrans({
    "i": "l", "1": "l", "|": "l", "!": "l",
    "0": "o", "3": "e", "4": "a", "5": "s", "7": "t",
})


def _skeleton(word: str) -> str:
    """`word` reduced to what a reader cannot tell apart."""
    return word.lower().translate(_SKELETON)


@functools.lru_cache(maxsize=None)
def _digest(word: str) -> str:
    return hashlib.sha256(_skeleton(word).encode()).hexdigest()[:16]


def _joined_names(word: str) -> "list[str]":
    """The names a word is made of when it is two or more names run
    together in one case (`firstnamesurname`, `FIRSTNAMESURNAME`), which
    the camel-case split cannot see. Only a word that splits WHOLLY into
    names counts, so a name inside an ordinary word is not one."""
    if len(word) < 6 or not (word.islower() or word.isupper()):
        return []
    return list(_split_into_names(word, _known(frozenset(_PEOPLE), _WORD_NAMES)))


@functools.lru_cache(maxsize=8)
def _known(people: frozenset, word_names: frozenset) -> frozenset:
    return people | word_names


@functools.lru_cache(maxsize=None)
def _split_into_names(word: str, known: frozenset) -> "tuple[str, ...]":
    # No name on the list is longer than this, so no longer piece is tried.
    longest = 16
    lower = word.lower()
    best: dict[int, tuple[str, ...]] = {0: ()}
    for end in range(3, len(lower) + 1):
        for start in range(max(0, end - longest), end - 2):
            if start in best and _digest(lower[start:end]) in known:
                best[end] = best[start] + (word[start:end],)
                break
    parts = best.get(len(lower))
    return parts if parts and len(parts) >= 2 else ()


def _people_named(text: str) -> "list[str]":
    """Every name on the list in `text`, whole words, the parts of a joined
    one, and a name hidden by encoding, by look-alike letters or by a
    character that prints as nothing, alike."""
    named = []
    for variant in _decodings(text):
        named += _people_in(_normalized(variant))
    return named


def _words(text: str) -> "list[tuple[str, int, int]]":
    """Every word of `text` and every part of one, as `(part, start, end)`."""
    out = []
    for match in _WORD.finditer(text):
        word = match.group(0)
        out.append((word, match.start(), match.end()))
        for part in _CAMEL.finditer(word):
            if part.group(0) != word:
                out.append((part.group(0), match.start() + part.start(), match.start() + part.end()))
        if len(word) <= 64:
            offset = 0
            for part in _joined_names(word):
                out.append((part, match.start() + offset, match.start() + offset + len(part)))
                offset += len(part)
    return out


def _people_in(text: str) -> "list[str]":
    named = []
    words = _words(text)
    local_parts = [(m.start(), m.end()) for m in _LOCAL_PART.finditer(text)]
    whole = [(w, s, e) for w, s, e in words if (s == 0 or not text[s - 1].isalnum()) and (e == len(text) or not text[e].isalnum())]
    for part, start, end in words:
        digest = _digest(part)
        if digest in _PEOPLE:
            named.append(part)
        elif digest in _WORD_NAMES and _a_name_by_context(text, part, start, end, whole, local_parts):
            named.append(part)
    return named


def _a_name_by_context(text, part, start, end, whole, local_parts) -> bool:
    """Whether an English-word first name is a name where it stands.

    Beside a surname or a handle, after a role word, inside `TODO(...)`, or
    in an email's local part. Nothing else: see the rule at `_PEOPLE`."""
    if _ROLE.search(text[:start]):
        return True
    if _TODO_OPEN.search(text[:start]) and _TODO_CLOSE.match(text[end:]):
        return True
    if any(low <= start and end <= high for low, high in local_parts):
        return True
    for other, other_start, other_end in whole:
        if _digest(other) not in _SURNAMES_AND_HANDLES:
            continue
        if other_start >= end and _BESIDE.match(text[end:other_start]):
            return True
        if other_end <= start and _BESIDE.match(text[other_end:start]):
            return True
    return False


# Every character a reference is really spelled with and a pattern does not
# read as itself: the hyphens and dashes of Unicode (U+2010 to U+2015, the
# minus sign U+2212, the small and fullwidth hyphen-minus), which read as the
# hyphen; and the characters that print as nothing (every format character,
# Unicode category Cf, which is the soft hyphen, the zero-width space and
# joiners, the byte-order mark and the invisible operators such as U+2064;
# and the Hangul fillers), which are removed. NFKC before either folds the
# fullwidth forms, the hash U+FF03 among them, and the modifier letters, into
# ASCII.
_AS_HYPHEN = dict.fromkeys(
    map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-"
)
_FILLERS = frozenset("\u115f\u1160\u3164\uffa0")
# The apostrophes a possessive is typed with.
_AS_APOSTROPHE = dict.fromkeys(map(ord, "\u2018\u2019\u02bc\uff07"), "'")
# The Cyrillic, Greek and IPA letters that print as Latin ones, and the Latin
# small capitals, and the dotless i. A repository or a person spelled with
# one of them is the same name to a reader and a different string to a
# pattern.
_CONFUSABLE = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0445\u0443\u043a\u043c\u0442\u043d\u0432\u0456\u0458\u0455\u04bb\u04cf"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0423\u0406\u0408\u0405\u04c0"
    "\u03bf\u03b1\u03b5\u03c1\u03c4\u03bd\u03ba\u03b9\u03c7\u03c5"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7"
    "\u0131\u0196\u01c0"
    "\u1d00\u0299\u1d04\u1d05\u1d07\ua730\u0262\u029c\u026a\u1d0a\u1d0b\u029f\u1d0d\u0274\u1d0f"
    "\u1d18\u0280\ua731\u1d1b\u1d1c\u1d20\u1d21\u028f\u1d22",
    "aeopcxykmthbijshl"
    "ABEKMHOPCTXYIJSl"
    "oaeptvkixu"
    "ABEZHIKMNOPTYX"
    "ill"
    "abcdefghijklmno"
    "prstuvwyz",
)


def _normalized(text: str) -> str:
    """`text` as a reader reads it: compatibility forms folded, combining
    marks stripped (so a mark NFKC would compose into a new letter cannot
    hide the letter under it), look-alike letters read as Latin, the
    characters that print as nothing removed, and the dashes and apostrophes
    a reference is typed with read as what they print as."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(
        c
        for c in decomposed
        if not unicodedata.category(c).startswith("M")
        and unicodedata.category(c) != "Cf"
        and c not in _FILLERS
    )
    return (
        unicodedata.normalize("NFKC", stripped)
        .translate(_CONFUSABLE)
        .translate(_AS_HYPHEN)
        .translate(_AS_APOSTROPHE)
    )


# Text a reference can be hidden in and still be read by anybody who pastes
# it into a decoder: percent-encoding and base64.
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")


def _decodings(text: str) -> "list[str]":
    """`text`, and every readable text it decodes to."""
    import base64
    import binascii
    import html
    from urllib.parse import unquote

    found = [text]
    if "%" in text:
        decoded = unquote(text)
        if decoded != text:
            found.append(decoded)
    if "&" in text:
        decoded = html.unescape(text)
        if decoded != text:
            found.append(decoded)
    for token in _BASE64.findall(text):
        padded = token + "=" * (-len(token) % 4)
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decode(padded)
                readable = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if readable and sum(c.isprintable() for c in readable) >= 0.9 * len(readable):
                found.append(readable)
                break
    return found


def forbidden_in(text: str) -> "list[tuple[str, str]]":
    """Each forbidden reference in `text`, as `(what, the text it matched)`,
    read after normalizing, in the text and in anything it decodes to. Every
    check in this module goes through here."""
    found = []
    for variant in _decodings(text):
        variant = _normalized(variant)
        for what, pattern in FORBIDDEN.items():
            match = pattern.search(variant)
            if match is not None and (what, match.group(0)) not in found:
                found.append((what, match.group(0)))
    return found


# Nothing is skipped by suffix. `git ls-files` lists what this repo ships and
# every one of those files is read: text as text, a PDF through its
# extractor, and anything else as bytes decoded permissively, because a
# format this module does not know is not a file a reader cannot pull strings
# out of.
PDF_SUFFIX = ".pdf"


def _tracked_files(root: Path = REPO_ROOT) -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    return [root / name for name in listed if name]


def _pdf_text(path: Path) -> str:
    """Everything a reader of this document would see, as lines.

    Extraction is not perfect and does not need to be: what it gives back is
    what a reader of the document extracts.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = [(page.extract_text() or "") for page in reader.pages]
    # What a reader sees without reading a page: the document's own
    # properties, and where each link goes.
    metadata = reader.metadata or {}
    for key in ("/Title", "/Author", "/Subject", "/Keywords", "/Creator", "/Producer"):
        value = metadata.get(key)
        if value:
            text.append(f"{key[1:]}: {value}")
    for page in reader.pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A") or {}
            action = action.get_object() if hasattr(action, "get_object") else action
            for key in ("/URI", "/F"):
                target = action.get(key)
                if target:
                    text.append(f"link: {target}")
    return "\n".join(text)


def _lines(path: Path) -> list[str]:
    if path.suffix.lower() == PDF_SUFFIX:
        return _pdf_text(path).splitlines()
    data = path.read_bytes()
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        # Not text this module knows how to decode, and still scanned. An
        # image comment chunk, a font name and a compiled string table all
        # carry readable characters, and passing the file over would bet that
        # a binary carries no text.
        return data.decode("latin-1").splitlines()


def _windows(lines: "list[str]") -> "list[tuple[int, str]]":
    """Each line, and each pair of adjacent lines joined by a space.

    A reference is a run of characters and a paragraph is wrapped at whatever
    width its author uses, so a name that fits on one line in the draft
    straddles two in the edit. The pair is what catches that, and it is why
    the separator class allows whitespace: `pull-` at the end of one line and
    `request` and its number at the start of the next is the same reference
    as either spelling on one line, and a guard reading line by line sees
    neither half.
    A finding on a pair is reported against the first of the two lines.
    """
    windows = [(number, line) for number, line in enumerate(lines, start=1)]
    pairs = []
    for index, (number, line) in enumerate(windows[:-1]):
        following = lines[index + 1]
        # Joined twice: as written, and with the second line's comment
        # marker taken off. Both, because a leading `#` is also how an issue
        # number wrapped onto the next line begins.
        for continued in _continuations(following):
            pairs.append((number, f"{line.rstrip()} {continued}"))
    return windows + pairs


# One marker that opens a line of prose continuing the line above it: a
# Python, shell or YAML comment, a blockquote, a `//` or `/*` comment and the
# `*` down the side of one, a SQL or Lua comment, an INI comment. Kept on the
# joined pair, the `# ` or `> ` of the second line would sit in the middle of
# the reference, and every wrapped reference inside a comment or a blockquote
# would pass. Most of this repo's prose is comments.
_MARKER = re.compile(r"^(?:#|>|//+|/\*+|\*+|--|;+)\s*")


def _continuations(line: str) -> "list[str]":
    """The second line of a pair as written, then with one opening marker
    taken off at a time.

    Every depth, not only the deepest, and one `#` or `>` per depth. A
    leading `#` is also how an issue number wrapped onto the next line
    begins, so `# #N` stripped of every `#` loses the hash the reference
    needs; the depth that removes only the comment's own marker keeps it. A
    run of markers counts as one depth per marker for the same reason:
    `##N` stripped as one `#+` step would go straight to `N`. Nested quoting
    (`> > `) is the case that needs more than one.
    """
    variants = [line.lstrip()]
    rest = line.lstrip()
    while True:
        stripped = _MARKER.sub("", rest, count=1)
        if stripped == rest:
            return variants
        rest = stripped.lstrip()
        variants.append(rest)


def scan(corpus: "dict[Path, list[str]]") -> list[str]:
    """Every forbidden reference in what it is given, and nothing else.

    It takes the text rather than fetching it, and it has no idea which file
    it is looking at beyond the name it reports. That is deliberate: an
    exemption is a thing this function cannot express.

    **The file's own name is read too.** A tracked file is a thing a reader
    sees before they open it, and a file named for an issue names that issue
    in the directory listing whatever its contents say. The fixture for it is
    built from parts like every other, in the control below.
    """
    findings: list[str] = []
    for path, lines in corpus.items():
        for what, found in forbidden_in(str(path)):
            findings.append(
                f"{path} is NAMED for {what} ({found!r}), so the "
                "reference is in the directory listing whatever the file says"
            )
        for person in _people_named(str(path)):
            findings.append(
                f"{path} is NAMED for a person ({person!r}), so the name is in "
                "the directory listing whatever the file says"
            )
        for line_number, line in enumerate(lines, start=1):
            for person in _people_named(line):
                findings.append(
                    f"{path}:{line_number} names a person by name ({person!r}): "
                    f"{line.strip()[:100]}"
                )
        # A name a paragraph wrapped: one that opens the second of two lines
        # (and so reads as the start of a sentence on that line alone), and
        # one hyphenated across them. Reported against the first line, and
        # only when neither line alone names it.
        for index in range(len(lines) - 1):
            first, second = lines[index], lines[index + 1]
            alone = set(_people_named(first)) | set(_people_named(second))
            for joined in _joined_lines(first, second):
                for person in _people_named(joined):
                    if person not in alone:
                        findings.append(
                            f"{path}:{index + 1} names a person by name across two "
                            f"lines ({person!r}): {joined.strip()[:100]}"
                        )
        for line_number, line in _windows(lines):
            for what, found in forbidden_in(line):
                findings.append(
                    f"{path}:{line_number} names "
                    f"{what} ({found!r}): {line.strip()[:100]}"
                )
    return findings


def _joined_lines(first: str, second: str) -> "list[str]":
    """Two adjacent lines as one, each way a paragraph joins them."""
    joined = [f"{first.rstrip()} {continued}" for continued in _continuations(second)]
    if first.rstrip().endswith("-"):
        joined += [f"{first.rstrip()[:-1]}{continued}" for continued in _continuations(second)]
    return joined


def corpus(root: Path = REPO_ROOT) -> "dict[Path, list[str]]":
    """Every tracked file, read. No file is left out and none is skipped.

    Read once per root and per session (`_read_corpus`): several tests scan
    the same tree, and reading and extracting every PDF again for each of
    them would be most of this module's time. A copy is handed back, so a test
    that changes what it was given changes nothing for the next one.
    """
    return {path: list(lines) for path, lines in _read_corpus(Path(root).resolve()).items()}


@functools.lru_cache(maxsize=8)
def _read_corpus(root: Path) -> "dict[Path, tuple[str, ...]]":
    read: dict[Path, tuple[str, ...]] = {}
    for path in _tracked_files(root):
        read[path.relative_to(root)] = tuple(_lines(path))
    return read


def test_no_tracked_file_names_a_repository_a_customer_cannot_open():
    findings = scan(corpus())

    assert not findings, (
        "this repo is published, and these lines point a reader at a "
        "repository they cannot open. State the rule in its own words "
        "instead.\n" + "\n".join(findings)
    )


HERE = Path(__file__).resolve().relative_to(REPO_ROOT)

# Made-up stand-ins for what the fixtures below point at: repositories that
# do not exist, and a number that is plainly not a real one. Any name would
# do, since a number in any repository is a reference.
_REPO = "zqxrepo"
_REPO_HYPHENATED = "zqx-archive"
_N = "9999"
# A reference every pattern set above reads as one, built from parts.
_PLANTED = f"see {_REPO}{_HASH}{_N} for the reason"


def test_this_guard_scans_itself_like_every_other_tracked_file():
    """This file has no exemption.

    A file the guard cannot see is where the next occurrence lands, and this
    file is tracked and published like any other. So a forbidden line filed
    under THIS file's own path is a finding like any other, and this file is
    in what the scan is given.
    """
    planted = {HERE: [_PLANTED]}

    assert scan(planted), (
        "a forbidden reference in this very file is not reported, so whatever "
        "lands here reaches a reader unchallenged"
    )
    assert HERE in corpus(), "this file is not in what the scan reads"
    assert len(corpus()[HERE]) > 100, "this file was not read as text"


def _reported_lines(findings: "list[str]") -> "dict[Path, set[int]]":
    """Which file, and which line of it, each line finding names."""
    reported: dict[Path, set[int]] = {}
    for finding in findings:
        match = re.match(r"(.+?):(\d+) names ", finding)
        if match:
            reported.setdefault(Path(match.group(1)), set()).add(int(match.group(2)))
    return reported


def test_the_scan_reports_on_every_line_of_every_tracked_file(monkeypatch):
    """Asserted on what `scan` REPORTS, not on what `corpus` hands it.

    A check that compared `corpus()` with git would stay green with a suffix
    skip put inside `scan` itself, because it would never run the scan. So
    the guard is run here, on the real tracked files, with a pattern that
    matches any character that is not a space, and every line of every file
    that carries one must come back as a finding at that line. A file `scan`
    passes over, or a line it drops, shows here as a missing number. And
    each file that is not a PDF was handed to the scan whole: what it
    reported on is exactly what the file's own bytes hold."""
    monkeypatch.setattr(sys.modules[__name__], "FORBIDDEN", {"anything": re.compile(r"\S")})
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    tracked = {Path(name) for name in listed if name}
    read = corpus()

    reported = _reported_lines(scan(read))

    assert tracked, "git lists nothing, so this test proves nothing"
    assert set(read) == tracked
    unreported = sorted(
        str(path)
        for path in tracked
        if not {n for n, line in enumerate(read[path], start=1) if line.strip()}
        <= reported.get(path, set())
    )
    assert unreported == [], f"the scan reports nothing on lines of these files: {unreported}"
    assert set(reported) == {path for path in tracked if any(line.strip() for line in read[path])}
    for path in sorted(tracked):
        if path.suffix.lower() == PDF_SUFFIX:
            continue
        data = (REPO_ROOT / path).read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        assert read[path] == text.splitlines(), f"{path} was handed to the scan short"


def test_the_two_pdfs_this_example_works_from_are_scanned_on_their_text(monkeypatch):
    """A skip for binary inputs would hide them, and they carry thousands of
    characters of text: one of them is the policy the agents reason from.

    Asserted on what `scan` reports: a pattern for a phrase that is only in
    each PDF's extracted text, run over the real tracked files. A `.pdf`
    skip in `corpus`, in `_lines` or inside `scan` itself leaves one of the
    two unreported."""
    from credit_demo.common import home

    phrases = {
        home.CREDIT_POLICY: "minimum DSCR for approval",
        home.ANNUAL_REPORT: "EBITDA",
    }
    monkeypatch.setattr(
        sys.modules[__name__],
        "FORBIDDEN",
        {f"the phrase {phrase!r}": re.compile(re.escape(phrase)) for phrase in phrases.values()},
    )

    findings = scan(corpus())

    for path, phrase in phrases.items():
        assert any(
            finding.startswith(f"{path}:") and phrase in finding for finding in findings
        ), f"{path} reached the guard with none of its text: {phrase!r} was not reported"


def _throwaway_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def test_a_tracked_file_that_is_not_utf8_is_read_by_the_guard_rather_than_skipped(
    tmp_path,
):
    """No file is skipped for its suffix, and this runs the guard to say so.

    An image can carry text as surely as a PDF does. A test that wrote its
    file into `tmp_path`, which nothing tracks, and called `_lines` on it
    would never run the guard, so a silent skip would leave it green.

    So the file is planted in a throwaway git repository, tracked there, and
    the guard is pointed at that repository. What is asserted is a finding
    out of `scan(corpus(root))`, which is the call the real test makes.
    """
    planted = tmp_path / "thing.png"
    planted.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe" + _PLANTED.encode() + b"\x00\xff")
    (tmp_path / "ordinary.md").write_text("nothing to see here\n")
    _throwaway_repo(tmp_path)

    findings = scan(corpus(tmp_path))

    assert any("thing.png" in finding for finding in findings), (
        "a tracked file that is not UTF-8 carried a forbidden reference and "
        f"the guard did not report it: {findings}"
    )


def test_the_patterns_catch_the_near_variants_too():
    """Near variants of a reference are the reference.

    Every fixture is built from parts and stand-ins, and the numbers are
    deliberately not real issue numbers: a fixture that reads as a live
    pointer is the very finding this guard makes.
    """
    issue = _N
    repo, other = _REPO, _REPO_HYPHENATED
    dash = chr(0x2011)
    planted = [
        f"see {repo}{_HASH}{issue} for the reason",
        f"see {repo.capitalize()}{_HASH}{issue} for the reason",
        f"see {repo} {_HASH}{issue} for the reason",
        f"see {repo} {_HASH} {issue} for the reason",
        f"as {other} issue {issue} records",
        f"as {other} issues {issue} records",
        f"see the {repo} repo's issue {issue}",
        f"issue {_HASH}{issue} in {repo}",
        f"filed in x/{other}{_HASH}9",
        f"{other}{_HASH}9 covers it",
        f"{other}-old{_HASH}9 covers it",
        f"{_HASH}9 covers it",
        f"ruled in issue{issue}",
        f"ruled in ISSUE-{issue}",
        f"ruled in issue {issue}",
        f"ruled in issue{dash}{issue}",
        f"the pull request {issue} entry",
        f"the pull-request {issue} entry",
        f"the PR {issue} entry",
        f"the ticket {issue} entry",
    ]

    missed = [line for line in planted if not forbidden_in(line)]

    assert missed == [], f"these lines reach a reader unchallenged: {missed}"


def test_a_reference_a_paragraph_broke_across_two_lines_is_still_a_reference():
    """How an 80-column markdown paragraph breaks, and the guard reads line
    by line. Each of these is one reference with a newline through it."""
    dash = chr(0x2011)
    repo = _REPO_HYPHENATED
    wrapped = [
        [f"the reason is recorded in {repo}", f"{_HASH}{_N}, which says why"],
        [f"the reason is recorded in {repo}{_HASH}", f"{_N}, which says why"],
        ["as ruled in issue", f"{_N} and not revisited"],
        ["as ruled in issue-", f"{_N} and not revisited"],
        ["the pull-", f"request {_N} has the entry"],
        ["see the pull", f"request {_N} for why"],
        [f"the pull{dash}", f"request {_N} says otherwise"],
    ]

    missed = [pair for pair in wrapped if not scan({Path("wrapped.md"): pair})]

    assert missed == [], (
        "a reference that a paragraph wrapped reaches a reader "
        f"unchallenged: {missed}"
    )


def test_a_reference_wrapped_inside_a_comment_or_a_blockquote_is_still_a_reference():
    """A pair that kept the second line's `# ` or `> ` would have it sit in
    the middle of the reference, so every wrapped reference inside a Python
    or YAML comment or a markdown blockquote would pass. Comments are most
    of this repo's prose."""
    repo = _REPO_HYPHENATED
    wrapped = []
    for marker in ("# ", "    # ", "> ", "> > ", "// ", " * ", "-- "):
        wrapped += [
            [f"{marker}as ruled in issue-", f"{marker}{_N} and not revisited"],
            [f"{marker}the reason is in {repo}", f"{marker}{_HASH}{_N}, which says why"],
            [f"{marker}the pull-", f"{marker}request {_N} has it"],
            [f"{marker}see the pull", f"{marker}request {_N} for why"],
        ]

    missed = [pair for pair in wrapped if not scan({Path("wrapped.py"): pair})]

    assert missed == [], (
        "a reference wrapped inside a comment or a blockquote reaches a "
        f"reader unchallenged: {missed}"
    )


def test_a_name_bounded_by_underscores_is_still_the_name():
    """`\\b` finds no boundary between `_` and a letter, so a guard bounded
    by it would pass these, in a file name and in a line alike, while it
    caught the hyphenated spellings of the same names."""
    named = [
        Path("docs") / ("issue_" + _N + "_notes.md"),
        Path("docs") / ("notes_issue" + _N + ".md"),
        Path("docs") / ("pr_" + _N + "_notes.md"),
        Path("docs") / ("notes_PR" + _N + ".md"),
    ]
    lines = [
        f"see issue_{_N}_notes for the reason",
        f"the notes_pr_{_N} file says so",
        f"notes_{_REPO} issue {_N} has the thread",
    ]

    missed_paths = [str(path) for path in named if not scan({path: ["nothing here"]})]
    missed_lines = [line for line in lines if not scan({Path("x.md"): [line]})]

    assert missed_paths == [], f"these file names reach a reader: {missed_paths}"
    assert missed_lines == [], f"these lines reach a reader: {missed_lines}"


def test_a_person_named_in_a_line_is_a_finding(monkeypatch):
    """The names themselves cannot be planted here, so a stand-in is added to
    the list for the length of this test and planted instead: what this
    shows is that every word of a line is looked up, in any case, and that a
    line naming nobody on the list passes."""
    stand_in = "Zqxplantedname"
    _with_stand_ins(monkeypatch, stand_in)

    assert scan({Path("x.md"): [f"a draft for {stand_in} to read"]})
    assert scan({Path("x.md"): [f"checked by {stand_in.upper()}."]})
    assert scan({Path("x.md"): ["a draft for the lender to read"]}) == []


def test_the_separators_a_reference_is_really_written_with():
    """Three spellings a reference is really written with: two spaces before
    the hash, an underscore where the hyphen goes, and the hyphen dropped."""
    repo = _REPO_HYPHENATED
    planted = [
        f"see {repo}  {_HASH}{_N}",
        f"see {repo} {_HASH}  {_N}",
        f"ruled in issue  {_N}",
        f"the pull_request {_N} entry",
        f"the pullrequest {_N} says otherwise",
        f"the PR_{_N} thread",
        f"the {repo.replace('-', '_')}{_HASH}{_N} ticket",
        f"the {repo.replace('-', '')}{_HASH}{_N} ticket",
    ]

    missed = [line for line in planted if not forbidden_in(line)]

    assert missed == [], f"these lines reach a reader unchallenged: {missed}"


def test_a_file_named_for_a_private_reference_is_a_finding_too():
    """A reader sees the file names in a directory listing before they open
    anything, so a tracked file named for an issue leaks it whatever the
    file says, clean contents included."""
    named = Path("docs") / ("issue-" + _N + "-notes.md")

    findings = scan({named: ["nothing in here says anything at all"]})

    assert findings, (
        "a tracked file named for an issue is not a finding, so the "
        "reference reaches a reader in the directory listing"
    )
    assert "is NAMED for" in findings[0], findings


def test_a_reference_to_a_pull_request_is_a_reference_too():
    """A pull request a reader cannot open is as much a dead end as an
    issue, so each of these is a reference too."""
    repo = _REPO_HYPHENATED
    planted = [
        f"see {repo} PR {_HASH}{_N} for why",
        f"see {repo} PR {_N} for why",
        f"see the {repo} pull request {_N}",
        f"see the {repo} pull-request {_HASH}{_N}",
        f"PR {_HASH}{_N} in {repo} has the thread",
        f"pull request {_N} on the {repo} repo",
        f"github.com/x/{repo}/issues/{_N}",
        f"github.com/x/{repo}/pull/{_N}",
        f"github.com/x/{repo}/issues/{_N[0]}{_HASH}issuecomment-1",
        f"../{repo}/pull/{_N}",
        f"../{repo}/issues/{_N}",
    ]

    missed = [line for line in planted if not scan({Path("x.md"): [line]})]

    assert missed == [], f"these lines reach a reader unchallenged: {missed}"


def test_the_unicode_separators_are_read_as_the_separators_they_print_as():
    """Normalized before anything is matched. A hyphen from anywhere in
    Unicode's dash block, a minus sign, a soft hyphen or a zero-width space
    prints a reference the patterns would not otherwise read as one, and so
    does a fullwidth hash."""
    repo = _REPO_HYPHENATED
    separators = {
        "hyphen U+2010": "\u2010",
        "figure dash U+2012": "\u2012",
        "em dash U+2014": "\u2014",
        "minus sign U+2212": "\u2212",
        "soft hyphen U+00AD": "\u00ad",
        "zero-width space U+200B": "\u200b",
    }
    missed = []
    for name, separator in separators.items():
        for line in (
            f"the pull{separator}request {_N} entry",
            f"ruled in issue{separator}{_N}",
            f"the PR{separator}{_N} thread",
            f"the ticket{separator}{_N} says otherwise",
        ):
            if not scan({Path("x.md"): [line]}):
                missed.append((name, line))
    fullwidth = f"see {repo}\uff03{_N} for why"
    if not scan({Path("x.md"): [fullwidth]}):
        missed.append(("fullwidth hash U+FF03", fullwidth))
    hidden = f"see {repo} {_HASH}\u200b{_N}"
    if not scan({Path("x.md"): [hidden]}):
        missed.append(("zero-width space after the hash", hidden))

    assert missed == [], f"these reach a reader unchallenged: {missed}"


def test_a_doubled_hash_is_stripped_one_marker_at_a_time():
    """`#+` would take `##N` to `N` in one step, so a reference wrapped onto
    a line that opens with a comment marker and a hash would lose the hash it
    needs. One marker per depth keeps the depth that has it."""
    repo = _REPO_HYPHENATED
    wrapped = [
        [f"# the reason is in {repo}", f"{_HASH}{_HASH}{_N}, which says why"],
        [f"the reason is in {repo}", f"## {_HASH}{_N}, which says why"],
    ]
    missed = [pair for pair in wrapped if not scan({Path("x.py"): pair})]
    one_line = scan({Path("x.md"): [f"see {repo} {_HASH}{_HASH}{_N}"]})

    # And the stripping itself, one marker per depth, since the pattern's own
    # tolerance for a run of hashes would otherwise hide a helper that
    # strips them all at once.
    assert _continuations(f"{_HASH}{_HASH}9999 x") == [
        f"{_HASH}{_HASH}9999 x", f"{_HASH}9999 x", "9999 x"
    ]

    assert missed == [], f"these reach a reader unchallenged: {missed}"
    assert one_line, "a doubled hash on one line reaches a reader unchallenged"


def _with_stand_ins(monkeypatch, *names: str) -> None:
    """Put stand-in names on the list for one test, since the real ones
    cannot be written here."""
    monkeypatch.setattr(
        sys.modules[__name__], "_PEOPLE", _PEOPLE | {_digest(name) for name in names}
    )


# Stand-ins for the first names that are also ordinary English words: a short
# form, a long form, and another short form. On that list only for a test
# that calls `_with_word_stand_ins`.
_WORD_NAME = "Zqxmill"
_WORD_NAME_LONG = "Zqxmillish"
_WORD_NAME_OTHER = "Zqxbolt"


def _with_word_stand_ins(monkeypatch) -> None:
    """Put the stand-ins on the list of first names that are also English
    words, for one test: each is a name only where its context says so."""
    stand_ins = (_WORD_NAME, _WORD_NAME_LONG, _WORD_NAME_OTHER)
    monkeypatch.setattr(
        sys.modules[__name__], "_WORD_NAMES", _WORD_NAMES | {_digest(name) for name in stand_ins}
    )


def test_a_person_named_in_a_file_name_is_a_finding(monkeypatch):
    _with_stand_ins(monkeypatch, "Zqxplantedname")

    for name in ("notes-for-Zqxplantedname.md", "zqxplantedname_draft.md",
                 "docs/Zqxplantedname/plan.md"):
        findings = scan({Path(name): ["nothing in here names anyone"]})
        assert any("is NAMED for a person" in f for f in findings), (name, findings)
    assert scan({Path("docs/plan.md"): ["nothing in here names anyone"]}) == []


def test_names_joined_in_camel_case_are_each_a_name(monkeypatch):
    _with_stand_ins(monkeypatch, "Zqxfirst", "Zqxlast")

    for line in ("signed by ZqxfirstZqxlast today", "the ZqxlastNotes file",
                 "see draftForZqxfirst"):
        assert scan({Path("x.md"): [line]}), line


def _stand_in_surname(monkeypatch, name: str) -> None:
    """A stand-in surname, on both lists, for one test."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_PEOPLE", _PEOPLE | {_digest(name)})
    monkeypatch.setattr(module, "_SURNAMES_AND_HANDLES", _SURNAMES_AND_HANDLES | {_digest(name)})


def test_an_english_word_first_name_is_a_name_where_its_context_says_so(monkeypatch):
    """The first names that are also English words are found beside a
    surname or a handle, after a role word, inside `TODO(...)` and in an
    email's local part, in any case and any spelling, and nowhere else."""
    _stand_in_surname(monkeypatch, "Zqxsurname")
    _with_word_stand_ins(monkeypatch)
    short, long_form, other = _WORD_NAME, _WORD_NAME_LONG, _WORD_NAME_OTHER
    named = [
        f"a draft for {short} to read",
        f"checked by {short}, then merged",
        f"Reviewer: {short}",
        f"    reviewer: {short.lower()}",
        f'owner = "{short}"',
        f"| Owner | Reviewer | Approver | {short} |",
        f"cc: {other}, does this read right?",
        f"## Notes From {short} On The Credit Memo",
        f"REVIEWED AND APPROVED BY {short.upper()} BEFORE RELEASE",
        f"TODO({short.upper()}): FIX THE MEMO LAYOUT",
        f"Co-authored-by: {short.lower()} <x@example.com>",
        f"send it to {short.lower()}@example.com",
        f"{short.lower()}.k@example.com",
        f"approved by {long_form}",
    ]
    missed = [line for line in named if not scan({Path("x.md"): [line]})]
    assert missed == [], f"these name a person and pass: {missed}"
    # Beside a surname, either side, and the name itself is the finding.
    for line in (f"{short} Zqxsurname signed it", f"Zqxsurname, {short} signed it", f"{short.lower()}.zqxsurname"):
        assert short.lower() in [p.lower() for p in _people_named(line)], line


# Lines this guard deliberately does not catch: the English-word first name
# alone, with no surname, handle, role word, TODO or email beside it. The
# same word is ordinary English, and a rule that tries to tell the two apart
# by case, position or punctuation lets a name through or flags ordinary
# English. Written with the stand-ins, which are on the list only while the
# test below runs.
NOT_CAUGHT_BY_DESIGN = [
    _WORD_NAME + " reviewed the layout.",
    "The memo is done. " + _WORD_NAME + " reviewed it on Monday.",
    _WORD_NAME_LONG + " says so.",
    "sent to " + _WORD_NAME_LONG,
    "sent to " + _WORD_NAME_OTHER + " on Monday",
    "the " + _WORD_NAME + "Notes page",
    "# " + _WORD_NAME + " it start",
    _WORD_NAME + " approved the memo, didn't he?",
    _WORD_NAME + " (the analyst) signed it?",
    _WORD_NAME + " can you confirm the figures?",
    "This is " + _WORD_NAME + "'s draft.",
    "https://example.com/people/" + _WORD_NAME + "/notes",
]


def test_an_english_word_first_name_alone_is_not_caught_and_that_is_the_rule(monkeypatch):
    """The stated limit, pinned so it is a decision and not a surprise."""
    _with_word_stand_ins(monkeypatch)
    flagged = [line for line in NOT_CAUGHT_BY_DESIGN if scan({Path("x.md"): [line]})]
    assert flagged == [], flagged


def test_ordinary_english_is_never_a_name(monkeypatch):
    """The sentences a first name that is also an English word appears in as
    that word, with a stand-in in the word's place, and the uses of the same
    word beside other words: none of them is a name by its context."""
    _with_word_stand_ins(monkeypatch)
    short, other = _WORD_NAME, _WORD_NAME_OTHER
    english = [
        f"{short} the checker agree?",
        f"The underwriter {short.lower()} retry.",
        f"THE UNDERWRITER {short.upper()} RETRY",
        f"## What The Checker {short} Do",
        f"{short} the file carry, e.g., a pack?",
        f"{short} version 0.9.0 still verify?",
        f"{short} README.md say so?",
        f"**{short}** it verify?",
        f'The auditor asks: "{short} the checker agree?"',
        f"a) {short} it verify offline?",
        f"It asks (as in: {short} it verify?) and moves on.",
        f"{short.upper()}_RETRY = True",
        f"def {short}Retry(): pass",
        f"the applicant's {short} and testament",
        f"## {other} Of Materials",
        f"A {other} of lading is attached.",
        f"{other} the customer monthly.",
        f"Free {short} is not in scope.",
        f"Good{short.lower()} is carried at cost.",
        f"{short}-call pickup is not offered.",
        f"{short} the service start with an empty file?",
        f"It fails. {short} it fail again? Yes.",
        f"the service {short.lower()} start with an empty file",
        f"Question: {short} it start with an empty file?",
        "TODO: " + short.lower() + " add a test for this",
    ]
    flagged = [line for line in english if scan({Path("x.md"): [line]})]
    assert flagged == [], f"these are ordinary English and are flagged: {flagged}"
    wrapped = [f"{short} the checker agree with the", "underwriter's reading of the pack?"]
    assert scan({Path("x.md"): wrapped}) == []


def test_a_name_spelled_with_look_alikes_or_invisible_characters_is_the_name(monkeypatch):
    """Spellings that hide a name: a dotless i, a capital I or a
    digit for an l, Latin small capitals, and a word joiner or a Hangul
    filler inside the name, for the English-word name in context and for a
    name found anywhere."""
    _with_word_stand_ins(monkeypatch)
    first, last = "Zqxfirst", "Zqxlast"
    _with_stand_ins(monkeypatch, first, last)
    short = _WORD_NAME
    for spelled in (
        short.replace("i", "\u0131"),
        short[:-2] + "II",
        short[:-2] + "11",
        short[:-3] + "\u026a\u029f\u029f",
        short[:4] + "\u2064" + short[4:],
        short[:4] + "\u3164" + short[4:],
        short[:4] + "\u200b" + short[4:],
    ):
        assert scan({Path("x.md"): [f"reviewed by {spelled} on Monday"]}), spelled
    for spelled in (
        first.replace("i", "\u0131"),
        first.replace("i", "I"),
        last.replace("l", "1"),
        last.replace("l", "I"),
        first[:3] + "\u2064" + first[3:],
        last[:3] + "\u3164" + last[3:],
    ):
        assert scan({Path("x.md"): [f"the draft {spelled} wrote"]}), spelled
        assert scan({Path("x.md"): [f"send it to {spelled.lower()}@example.com"]}), spelled
        assert scan({Path("x.md"): [f"Co-authored-by: {spelled.lower()} <x@example.com>"]}), spelled


def test_the_patterns_leave_what_a_reader_can_open_alone():
    """A guard that fires on the published package names, on ordinary prose,
    or on the stylesheets and comments this repo is made of, gets turned
    off."""
    allowed = [
        "pip install sanning-anchor sanning-proof",
        "https://console.sanning.io/health",
        "the underwriter agent writes an assessment",
        "a home-grown ledger of our own is out of scope",
        "the checker agent reviews the assessment",
        "each hand-off is confirmed at receipt",
        "the anchor issued by the plane",
        "an issue of 12 pages in the policy",
        "pull requests are welcome",
        "what the service will do on a restart",
        "  color: #555555;",
        "  border-bottom: 1px solid #999999;",
        "  border-top: 1px solid #333;",
        "      --ink: #14212B; --muted: #56636E; --card: #FFFFFF;",
        "  background: linear-gradient(#000000, #ffffff)",
        "    assert MONTHLY_RATE == 0.005  # 6.0% nominal annual",
        "    timeout = limit  # 30 seconds, then a retry",
        'assert "Agents&#39; recommendation" in page',
    ]

    for line in allowed:
        hit = [what for what, _found in forbidden_in(line)]
        assert hit == [], f"{line!r} is fine and this guard flags it as {hit}"


def test_an_issue_number_of_this_example_is_a_reference_too():
    """The example ships without its issue tracker, so its own issue and pull
    request numbers point at nothing a reader can open."""
    own = "credit-demo"
    planted = [
        f"{own}{_HASH}{_N} clears it for the lender",
        f"see {own} {_HASH}{_N} for the reason",
        f"see {own.replace('-', '_')}{_HASH}{_N} for the reason",
        f"see the {own} issue {_N}",
        f"see {own} PR {_N} for why",
        f"PR {_HASH}{_N} in {own} has the thread",
        f"github.com/x/{own}/issues/{_N}",
    ]
    missed = [line for line in planted if not forbidden_in(line)]
    assert missed == [], f"these lines reach a reader unchallenged: {missed}"
    # The name alone, and the command it names, stay readable.
    for line in (f"pip install -e . && {own} keys >> .env", f"{own}: not started"):
        assert forbidden_in(line) == [], line


def test_the_scan_reads_every_character_of_every_line_for_names_too(monkeypatch):
    """The whole-corpus test above swaps only the reference list, so the
    person scan's own loop could skip PDFs, read only `.md`, skip `.py`, read
    the first fifty lines or the first eighty characters of each, and that
    test would stay green. So here the LAST word of every line of every tracked file is put on
    the list of names, and each line must come back with a finding that names
    THAT word: a file, a line or a tail of a line the person scan does not
    read shows up as a missing word. The reference scan is held the same way,
    to the last characters of each line rather than to the first."""
    import ast

    read = corpus()
    last_words: dict[tuple[Path, int], str] = {}
    tails: dict[tuple[Path, int], str] = {}
    for path, lines in read.items():
        for number, line in enumerate(lines, start=1):
            normal = _normalized(line)
            words = _WORD.findall(normal)
            if words:
                last_words[(path, number)] = words[-1]
            if normal.strip():
                tails[(path, number)] = re.search(r".{0,19}\S(?=\s*$)", normal).group(0)
    everyone = {_digest(word) for word in last_words.values()}
    monkeypatch.setattr(sys.modules[__name__], "_PEOPLE", _PEOPLE | everyone)
    monkeypatch.setattr(
        sys.modules[__name__], "FORBIDDEN", {"the last characters": re.compile(r".{0,19}\S(?=\s*$)")}
    )

    findings = scan(read)

    def found(rest: str) -> str:
        """The repr a finding quotes, read back: the first `): ` that
        closes a whole literal, since the text itself can hold one."""
        for end in [m.start() for m in re.finditer(r"\): ", rest)]:
            try:
                return ast.literal_eval(rest[:end])
            except (SyntaxError, ValueError):
                continue
        raise AssertionError(rest)

    named: set[tuple[Path, int, str]] = set()
    ended: set[tuple[Path, int, str]] = set()
    for finding in findings:
        person = re.match(r"(.+?):(\d+) names a person by name \(", finding)
        if person:
            named.add((Path(person.group(1)), int(person.group(2)), found(finding[person.end():])))
        tail = re.match(r"(.+?):(\d+) names the last characters \(", finding)
        if tail:
            ended.add((Path(tail.group(1)), int(tail.group(2)), found(finding[tail.end():])))
    missed = sorted(f"{p}:{n} {w!r}" for (p, n), w in last_words.items() if (p, n, w) not in named)
    assert not missed, f"the person scan did not read the end of these lines: {missed[:20]}"
    missed = sorted(f"{p}:{n} {t!r}" for (p, n), t in tails.items() if (p, n, t) not in ended)
    assert not missed, f"the reference scan did not read the end of these lines: {missed[:20]}"
    # A PDF is among them, so both scans read extracted text too.
    assert any(path.suffix == PDF_SUFFIX for path, _ in last_words)


def test_the_ways_prose_writes_a_reference_are_each_a_reference():
    """Each of these is a reference the guard must read as one."""
    repo = _REPO_HYPHENATED
    issue, seven = _N, "7"
    planted = [
        f"{repo}'s issue {seven}",
        f"{repo}\u2019s issue {seven}",
        f"{repo} ticket {seven}",
        f"{repo} tickets {issue} and {issue}",
        f"{repo} issue {'seven'}",
        f"{repo} issue number {'twenty'}",
        f"see `{repo}`{_HASH}{issue} for why",
        f"see `{repo}` {_HASH}{issue} for why",
        f"see **{repo}**{_HASH}{issue} for why",
        f"see the {repo} repo {_HASH}{issue}",
        f"see the {repo} repository {_HASH}{issue}",
        f"{_HASH}{issue} in {repo} has the thread",
        f"see {repo} (PR {_HASH}{issue})",
        f"see {repo}, issue {issue}",
        f"see {repo}: {_HASH}{issue}",
        f"the {repo} repo\u2019s issue {issue}",
        f"issue {issue} from {repo}",
        f"issue {issue} for {repo}",
        f"PR {issue} ({repo})",
        f"see {repo} issue no. {issue}",
        f"see {repo} issue number {issue}",
    ]
    missed = [line for line in planted if not forbidden_in(line)]
    assert missed == [], f"these lines reach a reader unchallenged: {missed}"


def test_an_accent_a_look_alike_or_an_encoding_hides_neither_a_reference_nor_a_name(monkeypatch):
    """A combining mark NFKC composes into another letter, a Cyrillic or
    Greek look-alike, and a reference percent-encoded or in base64: each
    reads as the reference to a person, and the guard reads it as one too."""
    import base64

    repo = _REPO_HYPHENATED
    reference = f"{repo}{_HASH}{_N}"
    planted = [
        f"see {repo} issu\u0301e {_N}",
        f"see {repo} issue n{chr(0x043E)}. {_N}",
        f"see {repo} issue n{chr(0x03BF)}. {_N}",
        f"see {repo} {chr(0x0456)}ssue {_N}",
        "see " + reference.replace(_HASH, "%23"),
        "see " + base64.b64encode(f"see {reference} for why".encode()).decode(),
    ]
    missed = [line for line in planted if not forbidden_in(line)]
    assert missed == [], f"these lines reach a reader unchallenged: {missed}"

    stand_in = "Zqxplantedname"
    _with_stand_ins(monkeypatch, stand_in)
    for line in (
        f"a draft for {stand_in.replace('a', chr(0x0430))} to read",
        f"a draft for {stand_in.replace('e', chr(0x03B5))} to read",
        f"a draft for {stand_in.replace('e', 'e' + chr(0x0301))} to read",
        "a draft for Zqxpl\u00e1ntedn\u00e0me to read",
        "a draft for " + base64.b64encode(f"for {stand_in} to read".encode()).decode(),
        "a draft for " + stand_in[:4] + "%" + format(ord(stand_in[4]), "02x") + stand_in[5:],
    ):
        assert scan({Path("x.md"): [line]}), line


def test_a_name_run_together_shortened_or_split_across_lines_is_a_finding(monkeypatch):
    """Two names run together in one case, a first name that is also a word
    after a colon and in capitals, a short form found anywhere and a short
    form that is also a word, a name that opens a wrapped line, and a name
    hyphenated across two."""
    _with_stand_ins(monkeypatch, "Zqxfirst", "Zqxlast", "Zqxshort")
    _with_word_stand_ins(monkeypatch)
    short = _WORD_NAME
    short_one, short_two = "Zqxshort", _WORD_NAME_OTHER
    single = [
        "signed by zqxfirstzqxlast today",
        "signed by ZQXFIRSTZQXLAST today",
        f"Reviewer: {short}",
        f"checked by {short.upper()} today",
        f"a draft for {short_one} to read",
        f"cc {short_two}",
    ]
    missed = [line for line in single if not scan({Path("x.md"): [line]})]
    assert missed == [], f"these name a person and pass: {missed}"
    wrapped = [
        ["the draft was reviewed by", f"{short} before it merged"],
        ["> the draft was reviewed by", f"> {short} before it merged"],
        ["signed by Zqx-", "first today"],
    ]
    missed = [pair for pair in wrapped if not scan({Path("x.md"): pair})]
    assert missed == [], f"these name a person across two lines and pass: {missed}"
    # And a name inside an ordinary word is not a joined name.
    assert _joined_names("zqxfirstish") == []


def test_what_a_pdf_says_outside_its_pages_is_read(tmp_path, monkeypatch):
    """A PDF is read beyond `extract_text`: a document's Author, Subject,
    Title and Keywords, and where its links go, are all a reader sees without
    reading a page."""
    from fpdf import FPDF

    stand_in = "Zqxplantedname"
    _with_stand_ins(monkeypatch, stand_in)
    repo = _REPO_HYPHENATED
    for field in ("author", "subject", "title", "keywords"):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, "nothing on the page names anyone")
        getattr(pdf, f"set_{field}")(f"drafted by {stand_in}")
        (tmp_path / f"{field}.pdf").write_bytes(bytes(pdf.output()))
    linked = FPDF()
    linked.add_page()
    linked.set_font("Helvetica", size=12)
    linked.cell(0, 10, "a link", link=f"https://github.com/x/{repo}/issues/{_N}")
    (tmp_path / "linked.pdf").write_bytes(bytes(linked.output()))
    _throwaway_repo(tmp_path)

    findings = scan(corpus(tmp_path))

    for field in ("author", "subject", "title", "keywords"):
        assert any(f.startswith(f"{field}.pdf:") and "a person" in f for f in findings), (field, findings)
    assert any(f.startswith("linked.pdf:") and "a link to an issue" in f for f in findings), findings


def test_a_count_after_a_plural_verb_and_a_first_name_used_as_a_word_pass(monkeypatch):
    """A count after a plural verb is prose, and the English words that are
    also first names are prose wherever no name context surrounds them
    (`test_ordinary_english_is_never_a_name`)."""
    allowed = [
        "the agent pulls 2 documents from the store",
        "the service issues 3 requests a second",
    ]
    for line in allowed:
        hit = [what for what, _found in forbidden_in(line)]
        assert hit == [], f"{line!r} is fine and this guard flags it as {hit}"
    _with_word_stand_ins(monkeypatch)
    short, other = _WORD_NAME, _WORD_NAME_OTHER
    for line in (
        f"Question: {short} it start with an empty file?",
        f"## What The Checker {short} Do",
        f"THE UNDERWRITER {short.upper()} RETRY",
        f"## what {short.lower()} the checker do",
        f"the service {short.lower()} start with an empty file",
        f"the {other.lower()} of materials",
        f"{other.lower()} the customer monthly.",
    ):
        assert scan({Path("x.md"): [line]}) == [], line
    # And the cases these were meant to keep still read as references.
    repo, three, four = _REPO_HYPHENATED, "3", "4"
    assert forbidden_in(f"as {repo} issues {_N} records")
    assert forbidden_in(f"as {repo} issues {three} and {four} record")


def test_a_name_or_a_reference_in_a_heading_a_table_or_capitals_is_a_finding(monkeypatch):
    """Each line here names a person on the list or points at an issue by its
    number: in title case, in a table, in capitals, after a colon and at a
    sentence start, as a one-digit range after a plural, and with the
    separators and bridging words a reference is really written with."""
    _with_word_stand_ins(monkeypatch)
    short, other = _WORD_NAME, _WORD_NAME_OTHER
    repo, repo2 = _REPO, _REPO_HYPHENATED
    three, five, seven, nine = "3", "5", "7", "9"
    planted = [
        f"## Notes From {short} On The Credit Memo",
        f"Signed Off By {short} After The Review",
        f"| Owner | Reviewer | Approver | {short} |",
        f"REVIEWED AND APPROVED BY {short.upper()} BEFORE RELEASE",
        f"TODO({short.upper()}): FIX THE MEMO LAYOUT",
        f"Reviewer: {short}, can you confirm the figures?",
        f"cc: {other}, does this read right?",
        f"see {repo} pulls {five} through {nine}",
        f"{repo2} issues {three} to {seven} cover it",
        f"{repo} \u2014 issue {seven}",
        f"{repo} \u2013 issue {seven}",
        f"{repo} - issue {seven}",
        f"{repo}: see issue {seven}",
        f"issue {seven} over in {repo}",
    ]
    missed = [line for line in planted if not scan({Path("x.md"): [line]})]
    assert missed == [], f"these name a person or a reference and pass: {missed}"


def test_the_corpus_is_read_once_and_handed_out_as_a_copy():
    first = corpus()
    first[HERE].append("changed by a test")
    assert corpus()[HERE][-1] != "changed by a test"
    assert _read_corpus.cache_info().hits >= 1
