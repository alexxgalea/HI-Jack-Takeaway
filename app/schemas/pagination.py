from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """One page of a listing, plus what it took to select it.

    `total` counts every row the filters matched, not the rows returned, which
    is the whole reason the wrapper exists: a bare list cannot tell a caller
    whether a short page is the last one or the only one it asked for. With
    `total`, `limit` and `offset` echoed back, a client can page without
    guessing and without a second request to count.

    Generic so the three admin listings share one shape - `PaginatedResponse[OrderOut]`
    and `PaginatedResponse[UserOut]` are the same contract over different rows.
    """

    items: list[T]
    total: int
    limit: int
    offset: int
