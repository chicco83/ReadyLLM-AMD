"""Livello di astrazione degli esecutori

Unifica l'interfaccia di esecuzione locale e remota via SSH; tutte le funzioni operano sul Target configurato dall'utente
e non dipendono da alcun ambiente cablato nel codice.

Oltre all'esecuzione da riga di comando run(), fornisce write_file(): scrive contenuti voluminosi tramite scp (remoto) o file locale,
aggirando il limite di 8191 caratteri della riga di comando di Windows cmd.exe: i test di velocita' con prompt lunghi
devono passare da questo canale, altrimenti il comando con base64 incorporato verrebbe troncato.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from ..models.target import Target


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _decode(data: bytes) -> str:
    """Prova la decodifica UTF-8, in caso di errore ripiega su GBK (frequente nei sistemi Windows in cinese)"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


class Executor:
    """Classe base degli esecutori"""

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        raise NotImplementedError

    def write_file(self, content: str, path: str) -> bool:
        """Scrive il testo in UTF-8 nel path della macchina target (senza il limite di lunghezza della riga di comando)"""
        raise NotImplementedError

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        """Legge il contenuto binario di path sulla macchina target (per riportare al controller prodotti come il video finito). In caso di errore restituisce None."""
        raise NotImplementedError

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        """Scrive contenuto binario nel path della macchina target (per caricare prodotti come l'immagine del primo fotogramma). Restituisce True in caso di successo."""
        raise NotImplementedError

    def close(self):
        pass


class LocalExecutor(Executor):
    """Esecutore locale"""

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=timeout
            )
            return ExecResult(
                stdout=_decode(result.stdout).strip(),
                stderr=_decode(result.stderr).strip(),
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="", stderr="Timeout di esecuzione del comando", returncode=-1)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=-1)

    def write_file(self, content: str, path: str) -> bool:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(content.encode("utf-8"))
            return True
        except Exception:
            return False

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            return False


class SSHExecutor(Executor):
    """Esecutore SSH remoto (riusa i client ssh / scp di OpenSSH di sistema)

    Non usa paramiko: ssh/scp di sistema leggono correttamente ~/.ssh/config, usano le chiavi predefinite
    (nomi standard come id_ed25519), negoziano gli algoritmi con OpenSSH di Windows e offrono migliore compatibilita',
    evitando anche il blocco dell'handshake di paramiko che si verifica in certi ambienti.
    """

    def __init__(self, target: Target):
        self.target = target

    def _dest(self) -> str:
        return f"{self.target.user}@{self.target.host}"

    def _common_opts(self) -> list:
        # BatchMode=yes: quando serve interazione (password/conferma) fallisce subito invece di restare appeso per sempre,
        # essenziale per i servizi in background.
        return [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]

    def _identity(self) -> list:
        # Se e' indicata una chiave privata la usa; altrimenti lascia a ssh di sistema le chiavi predefinite (~/.ssh/id_ed25519 ecc.)
        if self.target.key_path:
            return ["-i", os.path.expanduser(self.target.key_path)]
        return []

    def _wrap_pw(self, base: list) -> list:
        """Con autenticazione a password e sshpass installato antepone sshpass; altrimenti restituisce invariato.
        Senza sshpass, BatchMode fa fallire rapidamente l'autenticazione a password invece di restare appeso."""
        t = self.target
        if t.auth_type == "password" and t.password and shutil.which("sshpass"):
            return ["sshpass", "-p", t.password] + base
        return base

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        argv = self._wrap_pw(
            ["ssh"] + self._common_opts() + self._identity()
            + ["-p", str(self.target.port), self._dest(), cmd]
        )
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout)
            return ExecResult(
                stdout=_decode(result.stdout).strip(),
                stderr=_decode(result.stderr).strip(),
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="", stderr="Timeout di esecuzione del comando SSH", returncode=-1)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=-1)

    def _scp_argv(self, src: str, dst: str) -> list:
        # scp usa -P per indicare la porta (diverso dal -p di ssh)
        base = (["scp"] + self._common_opts() + self._identity()
                + ["-P", str(self.target.port), src, dst])
        return self._wrap_pw(base)

    def write_file(self, content: str, path: str) -> bool:
        return self.write_file_bytes(content.encode("utf-8"), path)

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        """Carica tramite scp, aggirando il limite di lunghezza della riga di comando.

        Lo scp di OpenSSH per Windows accetta percorsi con barre dirette (es. C:/temp/bench.json),
        la cartella deve essere creata prima dal chiamante."""
        remote = path.replace("\\", "/")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp()
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            result = subprocess.run(
                self._scp_argv(tmp, f"{self._dest()}:{remote}"),
                capture_output=True, timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        """Scarica tramite scp un file della macchina target (es. riportare al controller il video finito mp4).

        Lo scp di OpenSSH per Windows accetta percorsi con barre dirette, il chiamante deve prima convertire le barre rovesciate."""
        remote = path.replace("\\", "/")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp()
            os.close(fd)
            result = subprocess.run(
                self._scp_argv(f"{self._dest()}:{remote}", tmp),
                capture_output=True, timeout=120,
            )
            if result.returncode != 0 or not os.path.exists(tmp):
                return None
            with open(tmp, "rb") as f:
                return f.read()
        except Exception:
            return None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def close(self):
        # Ogni chiamata di ssh/scp di sistema e' una connessione indipendente, non serve mantenere una connessione persistente
        pass


def make_executor(target: Target) -> Executor:
    """Crea l'esecutore corrispondente in base alla configurazione del Target"""
    if target.conn_type == "ssh":
        return SSHExecutor(target)
    return LocalExecutor()
