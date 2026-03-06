from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import asyncio
import os

from app.routes import matches, history, analysis
from app.routes import debug as debug_router
from app.result_poller import start_poller


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(start_poller())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="SLO·BET — Slovenian Football Analyzer",
    description="Análisis de apuestas PrvaLiga & 2.SNL con cuotas 1xbet y resultados automáticos",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(matches.router,  prefix="/api/matches",  tags=["Matches"])
app.include_router(history.router,  prefix="/api/history",  tags=["History"])
app.include_router(debug_router.router, tags=["Debug"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("frontend/index.html")

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "api_key_set": bool(os.getenv("API_FOOTBALL_KEY")),
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
