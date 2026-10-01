import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "apps.dashboard.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEV_RELOAD", "false").lower() == "true",
    )

