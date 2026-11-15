"""HTTP optimistic concurrency header parsers for Control API.

Authority:
- ADR-022: Agent draft concurrency and optimistic locking (D04-01).
- TP-STEP04: Strong ETag integer revision required on mutating operations.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

_STRONG_ETAG_INT_PATTERN = re.compile(r'^"([1-9][0-9]*)"$')
# catalog.agents.revision is PostgreSQL integer, not bigint.
_MAX_REVISION = 2_147_483_647


def parse_if_match_revision(request: Request) -> int:
    """Extract and validate the required strong ETag revision from If-Match header.

    Invariants:
    - Missing header -> 428 Precondition Required (not FastAPI's default 422).
    - Weak ETag (W/...), wildcard (*), list of tags, duplicate headers,
      or malformed syntax -> 400 Bad Request.
    - Present but empty header -> 400 Bad Request.
    - Out of range integer (<= 0 or > 2^31-1) -> 400 Bad Request.
    - Successfully returns the parsed positive integer revision.
    Does NOT query database or evaluate CAS staleness (stale revision 412 is
    evaluated during atomic persistence).
    """
    raw_headers = request.headers.getlist("if-match")

    if not raw_headers:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "precondition_required",
                "message": 'If-Match header is required with a strong entity-tag revision, e.g. If-Match: "1"',
            },
        )

    if len(raw_headers) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "Multiple If-Match headers are not permitted",
            },
        )

    header_value = raw_headers[0].strip()

    if not header_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "If-Match header must not be empty",
            },
        )

    # Reject wildcard
    if header_value == "*":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "Wildcard If-Match: * is not supported for optimistic concurrency",
            },
        )

    # Reject weak entity tags
    if header_value.startswith(("W/", "w/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "Weak entity-tags are not permitted in If-Match for mutation CAS",
            },
        )

    # Reject lists / comma-separated tags
    if "," in header_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "Multiple or comma-separated entity-tags are not permitted",
            },
        )

    match = _STRONG_ETAG_INT_PATTERN.match(header_value)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": 'If-Match must be a single strong entity-tag containing a positive integer, e.g. "1"',
            },
        )

    try:
        revision = int(match.group(1))
    except (ValueError, OverflowError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "If-Match revision value is not a valid integer",
            },
        )

    if revision > _MAX_REVISION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_if_match",
                "message": "If-Match revision exceeds PostgreSQL integer range",
            },
        )

    return revision


IfMatchRevision = Annotated[int, Depends(parse_if_match_revision)]

__all__ = ["IfMatchRevision", "parse_if_match_revision"]
