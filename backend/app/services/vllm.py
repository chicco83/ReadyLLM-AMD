"""Adattatore del motore vLLM

vLLM e' un motore di inferenza ad alto throughput, avvia un servizio compatibile OpenAI con `vllm serve <model>`.
Differenze chiave rispetto a llama.cpp:
  - Formato dei modelli: HuggingFace safetensors (ID modello HF o cartella dei pesi locali), non GGUF
  - Installazione: pip install vllm (dipende da Python + CUDA), non si scarica un binario
  - Limiti di piattaforma: non supporta l'esecuzione nativa su Windows, si distribuisce solo su Linux / macOS o WSL2

Percio' questo adattatore non tenta l'avvio su una macchina target Windows, ma restituisce un chiaro suggerimento per WSL2.
Tutti i comandi operano sul Target configurato dall'utente, senza alcun ambiente cablato nel codice.
"""

from .engine_adapter import EngineAdapter, StartParams
from .executor import Executor
from ..models.target import Target

# Parametri di avvio consigliati di default per vLLM (generici, senza alcuna macchina/modello specifico)
DEFAULT_ARGS = [
    "--max-model-len", "8192",
    "--host", "0.0.0.0",
]

WSL2_HINT = (
    "vLLM non supporta l'esecuzione nativa su Windows. Installare ed eseguire vLLM in WSL2 (Ubuntu), "
    "oppure cambiare il tipo di motore della macchina target in llama.cpp."
)


class VLLMAdapter(EngineAdapter):
    def __init__(self, executor: Executor, target: Target):
        self.executor = executor
        self.target = target

    def name(self) -> str:
        return "vllm"

    def _vllm_cmd(self) -> str:
        """Comando eseguibile di vllm: ha priorita' l'engine_path configurato dall'utente, altrimenti vllm nel PATH di default"""
        return self.target.engine_path or "vllm"

    # ==================== Rilevamento ====================

    def check_installed(self) -> bool:
        if self.target.os == "windows":
            # Windows nativo non supporta vLLM
            return False
        result = self.executor.run(f"{self._vllm_cmd()} --version 2>&1", timeout=20)
        out = (result.stdout or "").lower()
        # vllm --version stampa il numero di versione; se non installato segnala command not found / no module
        if result.ok and ("vllm" in out or any(c.isdigit() for c in out)):
            if "not found" not in out and "no module" not in out and "error" not in out.split("version")[0]:
                return True
        return False

    # ==================== Avvio / Arresto ====================

    def start(self, params: StartParams) -> tuple[bool, str]:
        if self.target.os == "windows":
            return False, WSL2_HINT

        args = list(params.extra_args) if params.extra_args else list(DEFAULT_ARGS)
        # Inietta la porta (vllm serve usa --port)
        if "--port" not in " ".join(args):
            args = args + ["--port", str(self.target.service_port)]
        args_str = " ".join(args)
        # Per vLLM model_path e' un ID modello HF o una cartella locale di safetensors
        model = params.model_path
        cmd = f'{self._vllm_cmd()} serve "{model}" {args_str}'

        # Avvio in background, log su disco
        run_cmd = f"nohup {cmd} > /tmp/vllm_server.log 2>&1 &"
        result = self.executor.run(run_cmd, timeout=20)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio di vLLM inviato (al primo caricamento del modello occorre scaricare i pesi, attendere con pazienza)"

    def stop(self) -> tuple[bool, str]:
        if self.target.os == "windows":
            return False, WSL2_HINT
        result = self.executor.run("pkill -f 'vllm serve'", timeout=10)
        if result.ok:
            return True, "Servizio vLLM fermato"
        return False, f"Esito dell'arresto: {result.stdout} {result.stderr}"

    def is_running(self) -> bool:
        if self.target.os == "windows":
            return False
        result = self.executor.run("pgrep -f 'vllm serve'")
        return bool(result.stdout.strip())

    # ==================== Monitoraggio ====================

    def get_metrics_url(self) -> str:
        # vLLM espone /metrics di default sulla --port (formato Prometheus)
        return f"http://127.0.0.1:{self.target.service_port}/metrics"
