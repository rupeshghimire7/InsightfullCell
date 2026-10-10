"""The geographic reference set the engine ships with, and its index.

Built offline from Natural Earth 5.1.2 (public domain) by ``scripts/build_geo_reference.py``:
countries (ISO 3166-1) and the states and provinces of nine countries (ISO 3166-2), with names,
abbreviations and simplified outlines. The file's SHA-256 is pinned here and in the
``ref.geo_reference_sets`` catalogue, and checked when the set is loaded; a new edition is a new
file with a new id, so facts always name the edition they were resolved with.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from shapely import STRtree, from_wkt
from shapely.geometry.base import BaseGeometry

REFERENCE_SET: Final = "ne-5.1.2"
REFERENCE_FILE: Final = "ne-5.1.2.json.gz"
REFERENCE_SHA256: Final = "68d70cff6deac106146b66c96b580005ee2989cea620c3c32b9e556d16db053f"

_PUNCTUATION: Final = re.compile(r"[^\w\s]")
_SPACES: Final = re.compile(r"\s+")
_PREFIXES: Final = ("state of ", "commonwealth of ", "the ")


@dataclass(frozen=True, slots=True)
class Region:
    level: str  # country or state
    code: str  # ISO 3166-1 alpha-2 or ISO 3166-2
    country: str  # ISO 3166-1 alpha-2
    name: str
    names: tuple[str, ...]  # full names and alternatives (fuzzy matching allowed)
    codes: tuple[str, ...]  # codes and abbreviations (exact matching only)


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a column's values are looked up: all countries, or one country's states."""

    level: str
    country: str | None

    @property
    def key(self) -> str:
        return self.level if self.country is None else f"{self.level}:{self.country}"


@dataclass(frozen=True, slots=True)
class Gazetteer:
    reference_set: str
    sha256: str
    regions: tuple[Region, ...]
    outlines: tuple[BaseGeometry, ...]  # by region position

    def scopes(self) -> list[Scope]:
        countries = sorted({r.country for r in self.regions if r.level == "state"})
        return [Scope("country", None), *(Scope("state", c) for c in countries)]

    def in_scope(self, scope: Scope) -> list[Region]:
        return [
            r
            for r in self.regions
            if r.level == scope.level and (scope.country is None or r.country == scope.country)
        ]

    def region(self, code: str) -> Region | None:
        return next((r for r in self.regions if r.code == code), None)


def normalise(value: str) -> str:
    """How place names are compared: case, accents, punctuation and spacing ignored, and
    "State of", "Commonwealth of" and "The" dropped ("N.S.W." -> "nsw", "Côte d'Ivoire" ->
    "cote divoire")."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = _SPACES.sub(" ", _PUNCTUATION.sub("", text.replace("&", " and "))).strip()
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


@cache
def load() -> Gazetteer:
    payload = resources.files("insightfulcell.engine.geo").joinpath("data", REFERENCE_FILE)
    raw = payload.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != REFERENCE_SHA256:
        raise RuntimeError(f"geographic reference set {REFERENCE_SET} does not match its digest")
    document = json.loads(gzip.decompress(raw))
    regions: list[Region] = []
    outlines: list[BaseGeometry] = []
    for item in document["regions"]:
        regions.append(
            Region(
                level=item["level"],
                code=item["code"],
                country=item["country"],
                name=item["name"],
                names=tuple(item["names"]),
                codes=tuple(item["codes"]),
            )
        )
        outlines.append(from_wkt(item["geometry"]))
    return Gazetteer(REFERENCE_SET, digest, tuple(regions), tuple(outlines))


@cache
def outline_index(level: str) -> tuple[STRtree, tuple[int, ...]]:
    """A spatial index over the outlines of one level, and each entry's region position."""
    gazetteer = load()
    positions = tuple(i for i, r in enumerate(gazetteer.regions) if r.level == level)
    return STRtree([gazetteer.outlines[i] for i in positions]), positions
