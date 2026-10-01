"""Adattatore del motore llama.cpp

Avvio/arresto/rilevamento dello stato si basano sul Target configurato dall'utente, adattati al sistema operativo di destinazione.
Su Windows si riusa la soluzione collaudata base64+bat+schtasks (risolve la codifica dei percorsi cinesi e il limite di lunghezza di schtasks).
Su Linux si avvia in background con nohup.
"""

import base64

from .engine_adapter import EngineAdapter, StartParams
from .executor import Executor
from .collectors import path_join
from ..models.target import Target

# Parametri predefiniti consigliati di llama-server (generici, senza alcuna macchina/percorso di modello specifico)
DEFAULT_ARGS = [
    "--ctx-size", "8192",
    "--flash-attn", "on",
    "--n-gpu-layers", "999",
    "--host", "0.0.0.0",
]


class LlamaCppAdapter(EngineAdapter):
    def __init__(self, executor: Executor, target: Target):
        self.executor = executor
        self.target = target

    def name(self) -> str:
        return "llama_cpp"

    def check_installed(self) -> bool:
        exe = self.target.engine_path
        if not exe:
            return False
        if self.target.os == "windows":
            result = self.executor.run(f'if exist "{exe}" (echo FOUND)')
            return "FOUND" in result.stdout
        else:
            result = self.executor.run(f'test -f "{exe}" && echo FOUND')
            return "FOUND" in result.stdout

    def start(self, params: StartParams) -> tuple[bool, str]:
        args = params.extra_args or list(DEFAULT_ARGS)
        # Inietta la porta
        if "--port" not in " ".join(args):
            args = args + ["--port", str(self.target.service_port)]
        args_str = " ".join(args)
        exe = self.target.engine_path
        model_path = params.model_path

        if self.target.os == "windows":
            return self._start_windows(exe, model_path, args_str)
        return self._start_linux(exe, model_path, args_str)

    def _start_windows(self, exe: str, model_path: str, args_str: str) -> tuple[bool, str]:
        # [2026-10-01 v1.1.8] Output di llama-server su C:\\temp\\llama_server.log (come su Linux): prima andava perso
        # e non si capiva perche' l'avvio falliva. Versione precedente:
        # bat_content = f'@echo off\r\n"{exe}" --model "{model_path}" {args_str}\r\n'
        bat_content = f'@echo off\r\n"{exe}" --model "{model_path}" {args_str} > C:\\temp\\llama_server.log 2>&1\r\n'
        b64 = base64.b64encode(bat_content.encode("gbk")).decode("ascii")
        bat_path = r"C:\temp\llama_start.bat"

        write_cmd = (
            f'powershell -Command "'
            f"New-Item -Path C:\\temp -ItemType Directory -Force | Out-Null; "
            f"[IO.File]::WriteAllBytes('{bat_path}', [Convert]::FromBase64String('{b64}'))"
            f'"'
        )
        result = self.executor.run(write_cmd, timeout=15)
        if not result.ok:
            return False, f"Scrittura dello script di avvio non riuscita: {result.stdout} {result.stderr}"

        if self.target.conn_type == "local":
            # [2026-10-01 v1.1.12] Target locale: finestra NASCOSTA. Prima (e con il log su file) si apriva una shell nera
            # vuota che sembrava un blocco: l'output di llama-server e' nel file C:\\temp\\llama_server.log, visibile
            # dal pulsante «Log del motore» nella pagina Deploy. Per i target SSH resta schtasks (il processo deve
            # sopravvivere alla chiusura della sessione SSH).
            run_cmd = (f'powershell -NoProfile -Command "Start-Process -FilePath \'{bat_path}\' '
                       f'-WindowStyle Hidden"')
        else:
            run_cmd = (
                'schtasks /create /tn LlamaServer /tr "%s" /sc once /st 00:00 /f '
                '&& schtasks /run /tn LlamaServer' % bat_path
            )
        # Versione precedente (sempre schtasks, finestra visibile):
        # run_cmd = ('schtasks /create /tn LlamaServer /tr "%s" /sc once /st 00:00 /f '
        #            '&& schtasks /run /tn LlamaServer' % bat_path)
        result = self.executor.run(run_cmd, timeout=15)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio inviato"

    def _start_linux(self, exe: str, model_path: str, args_str: str) -> tuple[bool, str]:
        cmd = (
            f'nohup "{exe}" --model "{model_path}" {args_str} '
            f'> /tmp/llama_server.log 2>&1 &'
        )
        result = self.executor.run(cmd, timeout=15)
        if not result.ok:
            return False, f"Avvio non riuscito: {result.stdout} {result.stderr}"
        return True, "Comando di avvio inviato"

    def stop(self) -> tuple[bool, str]:
        if self.target.os == "windows":
            result = self.executor.run("taskkill /f /im llama-server.exe", timeout=10)
        else:
            result = self.executor.run("pkill -f llama-server", timeout=10)
        if result.ok:
            return True, "Servizio fermato"
        return False, f"Esito dell'arresto: {result.stdout} {result.stderr}"

    def is_running(self) -> bool:
        if self.target.os == "windows":
            result = self.executor.run('tasklist /fi "imagename eq llama-server.exe" /fo csv /nh')
            return "llama-server.exe" in result.stdout
        else:
            result = self.executor.run("pgrep -f llama-server")
            return bool(result.stdout)

    def get_metrics_url(self) -> str:
        # La porta delle metriche e' accessibile solo localmente sulla macchina target; durante la raccolta si esegue curl su questo indirizzo sulla macchina target
        return f"http://127.0.0.1:{self.target.service_port}/metrics"
