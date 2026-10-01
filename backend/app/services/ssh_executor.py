"""Esecutore di comandi remoti via SSH (riusa la logica esistente di llama_monitor)"""

import subprocess
from dataclasses import dataclass


@dataclass
class SSHResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _decode(data: bytes) -> str:
    """Prova la decodifica UTF-8, in caso di errore ripiega su GBK"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def ssh_cmd(
    cmd: str,
    host: str = "192.168.50.223",
    user: str = "htob",
    timeout: int = 15,
) -> SSHResult:
    """Esegue un comando in remoto tramite SSH"""
    full_cmd = [
        "ssh",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        f"{user}@{host}",
        cmd,
    ]
    try:
        result = subprocess.run(full_cmd, capture_output=True, timeout=timeout)
        return SSHResult(
            stdout=_decode(result.stdout).strip(),
            stderr=_decode(result.stderr).strip(),
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return SSHResult(stdout="", stderr="Timeout SSH", returncode=-1)
    except Exception as e:
        return SSHResult(stdout="", stderr=str(e), returncode=-1)
