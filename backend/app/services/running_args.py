"""Parametri con cui llama-server e' stato avviato per macchina (v1.1.32, 2026-10-02).

Scritti sia dal Deploy (avvio manuale) sia dal tuner (a ogni prova), cosi' la scheda «Decodifica speculativa» del Monitoraggio mostra
il tipo realmente in uso anche DURANTE il tuning. File: ~/.model-deploy-assistant/running_args.json -> {target_id: [args...]}.
"""
import json
import os

_FILE = os.path.expanduser("~/.model-deploy-assistant/running_args.json")


def record(target_id: str, args: list) -> None:
    try:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            d = {}
        d[target_id] = [str(a) for a in args]
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def get(target_id: str):
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get(target_id)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return None
