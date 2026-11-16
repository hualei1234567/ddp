"""场地域：多基地的场地档案（N9）。"""

from app.domain.locations.enums import DEFAULT_LOCATION_KIND, LocationKind
from app.domain.locations.errors import (
    DomainValidationError,
    DuplicateResourceError,
    IdempotencyKeyReusedError,
    LocationDomainError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.locations.models import PATCHABLE_FIELDS, Location
from app.domain.locations.validation import (
    ADDRESS_MAX_LENGTH,
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    NOTES_MAX_LENGTH,
)

__all__ = [
    "ADDRESS_MAX_LENGTH",
    "CODE_MAX_LENGTH",
    "DEFAULT_LOCATION_KIND",
    "NAME_MAX_LENGTH",
    "NOTES_MAX_LENGTH",
    "PATCHABLE_FIELDS",
    "DomainValidationError",
    "DuplicateResourceError",
    "IdempotencyKeyReusedError",
    "Location",
    "LocationDomainError",
    "LocationKind",
    "ResourceNotFoundError",
    "VersionConflictError",
]
