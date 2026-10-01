"""Punto di ingresso dell'applicazione FastAPI"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import hardware, deploy, monitor, target, store, tune, ai_tune

app = FastAPI(title="Assistente di deploy per LLM locali", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(target.router, prefix="/api/target", tags=["Macchina target"])
app.include_router(hardware.router, prefix="/api/hardware", tags=["Hardware"])
app.include_router(deploy.router, prefix="/api/deploy", tags=["Deploy"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["Monitoraggio"])
app.include_router(store.router, prefix="/api/store", tags=["Negozio modelli"])
app.include_router(tune.router, prefix="/api/tune", tags=["Tuning intelligente"])
app.include_router(ai_tune.router, prefix="/api/ai-tune", tags=["Tuning AI"])


@app.get("/")
def root():
    return {"name": "Assistente di deploy per LLM locali", "version": "0.1.0"}
