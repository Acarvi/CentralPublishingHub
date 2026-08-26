import os
import sys
import threading

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

from contextlib import asynccontextmanager
from fastapi import FastAPI
from routers import publish
from core.publisher import recover_interrupted_posts, scheduler_loop

_scheduler_stop = threading.Event()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _scheduler_stop.clear()
    recovered = recover_interrupted_posts()
    if recovered:
        print(f'[WARN] {recovered} trabajos interrumpidos marcados como unknown (requieren conciliación).')
    worker = threading.Thread(target=scheduler_loop, args=(_scheduler_stop,), daemon=True)
    worker.start()
    yield
    _scheduler_stop.set()
    worker.join(timeout=2)

app = FastAPI(
    title="Central Publishing Hub",
    description="Microservicio centralizado para publicar contenido en redes sociales.",
    version="1.0.0",
    lifespan=lifespan,
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
    port = int(os.environ.get("PORT", 8766))
    uvicorn.run(app, host="0.0.0.0", port=port)
