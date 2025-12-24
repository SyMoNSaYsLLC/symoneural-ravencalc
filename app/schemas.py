from pydantic import BaseModel, Field
from typing import Optional, List

class ComputeRequest(BaseModel):
    operation: str = Field(..., description="simplify|differentiate|integrate|limit|solve")
    expression: str = Field(..., min_length=1, max_length=4000)

    # optional parameters
    variables: Optional[List[str]] = None   # e.g. ["x","y"]
    wrt: Optional[str] = None               # with respect to
    n: Optional[int] = 1                    # derivative order
    a: Optional[str] = None                 # integral lower
    b: Optional[str] = None                 # integral upper
    limit_to: Optional[str] = None          # limit point

class ComputeResponse(BaseModel):
    ok: bool
    operation: str
    result: str
    latex: Optional[str] = None
    detail: Optional[str] = None
