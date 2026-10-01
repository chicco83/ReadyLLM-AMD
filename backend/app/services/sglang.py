"""Adattatore del motore SGLang

SGLang e' un framework di inferenza ad alto throughput, avvia un servizio compatibile OpenAI con `sglang serve <model>`.
Differenze chiave rispetto a llama.cpp / vLLM (dalla documentazione ufficiale docs.sglang.io):
  - Formato dei modelli: pesi HuggingFace (ID modello HF o cartella dei pesi locali), non GGUF
  - Comando di avvio: il modello e' un argomento posizionale, ad es. `sglang serve MODEL --host 0.0.0.0 --port 30000`
  - Metriche: non esposte di default, /metrics esiste solo se si avvia con --enable-metrics
    (testo Prometheus, prefisso delle metriche sglang:, con etichetta model_name)
  - Piattaforma: le istruzioni ufficiali di installazione riguardano Linux + GPU NVIDIA, su Windows serve WSL2

Tutti i comandi operano sul Target configurato dall'utente, senza alcun ambiente cablato nel codice.
"""

from .engine_adapter import EngineAdapter, StartParams
from .executor import Executor
from ..models.target import Target

# Parametri di avvio consigliati di default per SGLang (generici, senza alcuna macchina/modello specifico)
# --enable-metrics e' il presupposto della raccolta di monitoraggio: senza, /metrics non espone le metriche
DEFAULT_ARGS = [
    "--host", "0.0.0.0",
    "--enable-metrics",
]

WSL2_HINT = (
    "Le istruzioni ufficiali di installazione di SGLang riguardano Linux + GPU NVIDIA. Installarlo ed eseguirlo in WSL2 (Ubuntu), "
    "oppure cambiare il tipo di motore della macchina target in llama.cpp."
)


class SGLangAdapter(EngineAdapter):
    def __init__(self, executor: Executor, target: Target):
        self.executor = executor
        self.target = target

    def name(self) -> str:
        return "sglang"

    def _sglang_cmd(self) -> str:
        """Comando eseguibile di sglang: ha priorita' l'engine_path configurato dall'utente, altrimenti sglang nel PATH di default"""
        return self.target.engine_path or "sglang"

    # ==================== Rilevamento ====================

    def check_installed(self) -> bool:
        if self.target.os == "windows":
            # Windows nativo non supporta SGLang
            return False
        result = self.executor.run(f"{self._sglang_cmd()} --version 2>&1", timeout=25)
        out = (result.stdout or "").lower()
        if "not found" in out or "no module" in out or "command not found" in out:
            return False
        return bool(result.ok and ("sglang" in out or any(c.isdigit() for c in out)))

    # ==================== Avvio / Arresto ====================

    def start(self, params: StartParams) -> tuple[bool, str]:
        if self.target.os == "windows":
            return False, WSL2_HINT

        args = list(params.extra_args) if params.extra_args else list(DEFAULT_ARGS)
        # Il monitoraggio dipende da --enable-metrics: se i parametri personalizzati dell'utente non lo includono, lo si aggiunge
        if "--enable-metrics" not in args:
            args = args + ["--enable-metrics"]
        # Inietta la porta (sglang serve usa --port)
        if "--port" not in args:
            args = args + ["--port", str(self.target.service_port)]
        args_str = " ".join(args)

        # Per SGLang model_path e' un ID modello HF o una cartella di pesi locali (argomento posizionale)
        model = params.model_path
        cmd = f'{self._sglang_cmd()} serve "{model}" {args_str}'

        # Avvio in background, log su disco
        run_cmd = f"nohup {cmd} > /tmp/sglang_server.log 2>&1 &"
        result = self.executor.run(run_cmd, timeout=20)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio di SGLang inviato (al primo caricamento del modello occorre scaricare i pesi, attendere con pazienza)"

    def stop(self) -> tuple[bool, str]:
        if self.target.os == "windows":
            return False, WSL2_HINT
        # sglang serve genera sottoprocessi scheduler / detokenizer, si terminano insieme cercandoli per nome
        result = self.executor.run("pkill -f 'sglang'", timeout=10)
        if result.ok:
            return True, "Servizio SGLang fermato"
        return False, f"Esito dell'arresto: {result.stdout} {result.stderr}"

    def is_running(self) -> bool:
        if self.target.os == "windows":
            return False
        result = self.executor.run("pgrep -f 'sglang'")
        return bool(result.stdout.strip())

    # ==================== Monitoraggio ====================

    def get_metrics_url(self) -> str:
        # SGLang espone /metrics sulla --port (richiede l'avvio con --enable-metrics)
        return f"http://127.0.0.1:{self.target.service_port}/metrics"