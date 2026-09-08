from __future__ import annotations

from enum import StrEnum


class ChannelCapability(StrEnum):
    SEARCH = "SEARCH"
    DRAFT = "DRAFT"
    PUBLISH = "PUBLISH"
    MEASURE = "MEASURE"


class PublisherMode(StrEnum):
    MANUAL = "MANUAL"
    CLIENT_OWNED = "CLIENT_OWNED"
    PARTIZAN_MANAGED = "PARTIZAN_MANAGED"
