from dotenv import load_dotenv
load_dotenv()

import os
import uvicorn

# app.ai imports app.main and adds the natural-language routes to that same
# FastAPI instance, so serving app.ai serves everything. The Christmas backend
# is untouched; set RAVENCALC_NO_AI=1 to serve app.main:app bare instead.
TARGET = "app.main:app" if os.environ.get("RAVENCALC_NO_AI") else "app.ai:app"

if __name__ == "__main__":
    uvicorn.run(
        TARGET,
        host=os.environ.get("RAVENCALC_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
