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
from app.routes import arg_matches
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
    title="SLO·BET + ARG·BET — Football Analyzer",
    description="Análisis PrvaLiga, 2.SNL, Primera División & Primera Nacional con cuotas 1xbet",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Slovenia routes ────────────────────────────────────────────────────────
app.include_router(matches.router,      prefix="/api/matches",      tags=["SLO Matches"])
app.include_router(history.router,      prefix="/api/history",      tags=["SLO History"])
app.include_router(analysis.router,     prefix="/api/analysis",     tags=["SLO Analysis"])
app.include_router(debug_router.router, tags=["Debug"])

# ── Argentina routes ───────────────────────────────────────────────────────
app.include_router(arg_matches.router,  prefix="/api/arg/matches",  tags=["ARG Matches"])
# Argentina re-uses same history table with league filter
app.include_router(history.router,      prefix="/api/arg/history",  tags=["ARG History"])

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("frontend/index.html")

@app.get("/arg", include_in_schema=False)
async def arg_root():
    return FileResponse("frontend/arg-bet.html")

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "api_key_set": bool(os.getenv("API_FOOTBALL_KEY")),
        "worker_set": bool(os.getenv("XBET_WORKER_URL")),
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
