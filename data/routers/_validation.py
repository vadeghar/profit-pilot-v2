"""Shared query-parameter validation guard for data routers.

FastAPI silently ignores unknown query parameters. A misspelled parameter
(e.g. ``opttionType=PE`` instead of ``optionType=PE``) would be dropped and
the endpoint would silently fall back to its defaults, producing confusing
results. This guard rejects unknown query parameters with a 422 error that
lists the valid parameter names, so typos surface immediately.
"""
from fastapi import HTTPException, Request


def guard_query_params(*allowed: str):
    """
    Return a FastAPI dependency that rejects unknown query parameters.

    Args:
        *allowed: Names of the query parameters the endpoint accepts.

    Returns:
        Callable dependency for ``APIRouter(dependencies=[...])``.
    """
    allowed_set = set(allowed)

    async def guard(request: Request) -> None:
        extras = sorted(k for k in request.query_params.keys() if k not in allowed_set)
        if extras:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown query parameter(s): {', '.join(extras)}. "
                    f"Valid query params: {', '.join(sorted(allowed_set))}. "
                    f"Note: parameters must be passed as query params, not in the request body."
                ),
            )

    return guard
