"""API di rilevamento hardware (basata sul Target configurato dall'utente)"""

from fastapi import APIRouter, HTTPException

from ..models.target import get_target
from ..services.executor import make_executor
from ..services.collectors import detect_hardware

router = APIRouter()


def _resolve(target_id: str):
    target = get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Macchina target inesistente, configurarla prima nelle Impostazioni")
    return target, make_executor(target)


@router.get("/detect")
def api_detect(target_id: str):
    """Rileva le informazioni hardware della macchina target"""
    target, executor = _resolve(target_id)
    try:
        return detect_hardware(executor, target)
    finally:
        executor.close()


@router.get("/recommend")
def api_recommend(target_id: str):
    """Raccomanda modelli in base all'hardware della macchina target"""
    target, executor = _resolve(target_id)
    try:
        hw = detect_hardware(executor, target)
    finally:
        executor.close()

    gpu = hw.get("gpu") or {}
    gpu_mem_gb = gpu.get("total_memory_gb", 0)
    recommendations = []

    if gpu_mem_gb >= 24:
        recommendations += [
            {"model": "Qwen3-27B-Q4_K_M", "size_gb": 16.3, "expected_speed": "15-25 t/s", "fit": "VRAM abbondante, puo' eseguire modelli grandi"},
            {"model": "Qwen3-8B-Q8_0", "size_gb": 8.5, "expected_speed": "40-60 t/s", "fit": "Priorita' alla velocita', quantizzazione di alta qualita'"},
        ]
    elif gpu_mem_gb >= 16:
        recommendations += [
            {"model": "Qwen3-14B-Q4_K_M", "size_gb": 9.0, "expected_speed": "25-40 t/s", "fit": "Scelta equilibrata"},
            {"model": "Qwen3-8B-Q8_0", "size_gb": 8.5, "expected_speed": "40-60 t/s", "fit": "Priorita' alla velocita'"},
        ]
    elif gpu_mem_gb >= 8:
        recommendations += [
            {"model": "Qwen3-8B-Q4_K_M", "size_gb": 5.0, "expected_speed": "30-50 t/s", "fit": "VRAM limitata, consigliato 8B"},
        ]
    elif gpu_mem_gb > 0:
        recommendations += [
            {"model": "Qwen3-4B-Q4_K_M", "size_gb": 2.5, "expected_speed": "50-80 t/s", "fit": "VRAM ridotta, consigliato 4B"},
        ]

    return {"hardware": hw, "recommendations": recommendations}
