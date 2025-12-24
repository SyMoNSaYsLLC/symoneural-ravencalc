from pydantic import BaseModel, Field
from typing import Optional, List


class ComputeRequest(BaseModel):
    operation: str = Field(
        ...,
        description="simplify | differentiate | integrate | limit | solve"
    )

    expression: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Main expression, e.g. sin(x)/x or sin(x)/x, x->0"
    )

    # Optional parameters
    variables: Optional[List[str]] = Field(
        default=None,
        description="Explicit variables, e.g. ['x','y']"
    )

    wrt: Optional[str] = Field(
        default=None,
        description="With respect to (x by default)"
    )

    n: Optional[int] = Field(
        default=1,
        description="Derivative order"
    )

    a: Optional[str] = Field(
        default=None,
        description="Integral lower bound"
    )

    b: Optional[str] = Field(
        default=None,
        description="Integral upper bound"
    )

    limit_to: Optional[str] = Field(
        default=None,
        description="Limit point (e.g. 0, 0+, pi)"
    )

    limit_dir: Optional[str] = Field(
        default=None,
        description="Optional limit direction: '+' or '-'"
    )

    # NEW: ask backend to return "lightweight steps"
    show_steps: Optional[bool] = Field(
        default=False,
        description="If true, backend includes lightweight steps (best effort)."
    )


class ComputeResponse(BaseModel):
    ok: bool
    operation: str
    result: str
    latex: Optional[str] = None
    detail: Optional[str] = None

    # NEW: steps (best effort, not guaranteed for all expressions)
    steps: Optional[List[str]] = None