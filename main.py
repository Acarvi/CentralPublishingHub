import os
import sys

# --- SENTINEL API BOOTSTRAP ---
SENTINEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "SentinelAPI"))
if SENTINEL_PATH not in sys.path:
    sys.path.insert(0, SENTINEL_PATH)
try:
    from bootstrap import activate_security
    activate_security()
except ImportError:
    print("Warning: SentinelAPI module not found. Proceeding with caution.")

from fastapi import FastAPI
from routers import publish

app = FastAPI(
    title="Central Publishing Hub",
    description="Microservicio centralizado para publicar contenido en redes sociales.",
    version="1.0.0"
)

app.include_router(publish.router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"status": "Central Publishing Hub Running"}

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "CentralPublishingHub"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
