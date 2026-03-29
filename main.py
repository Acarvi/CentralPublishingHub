from fastapi import FastAPI
from routers import publish

app = FastAPI(
    title="Central Publishing Hub",
    description="Microservicio centralizado para publicar contenido en redes sociales.",
    version="1.0.0"
)

app.include_router(publish.router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "CentralPublishingHub"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
