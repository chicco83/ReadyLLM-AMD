"""Modello di configurazione delle macchine target

Strumento generico per tutti gli utenti: macchina target, percorso del motore, cartella dei modelli, porta
sono tutti configurati dall'utente, e' vietato cablare nel codice qualsiasi ambiente specifico.
"""

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

# Cartella di persistenza della configurazione (cartella home dell'utente, non quella del progetto)
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".model-deploy-assistant")
CONFIG_FILE = os.path.join(CONFIG_DIR, "targets.json")


@dataclass
class Target:
    """Configurazione della macchina target"""
    # Tipo di connessione: local (locale) o ssh (remota)
    conn_type: str = "local"

    # Parametri di connessione SSH (usati quando conn_type=ssh)
    host: str = ""
    port: int = 22
    user: str = ""
    auth_type: str = "key"          # key (chiave) o password
    key_path: str = ""              # percorso della chiave privata, default ~/.ssh/id_rsa
    password: str = ""              # usata con l'autenticazione a password

    # Tipo di sistema target: windows / linux
    os: str = "linux"

    # Tipo di motore di inferenza: llama_cpp / vllm (default llama_cpp, retrocompatibile con le vecchie configurazioni)
    engine_type: str = "llama_cpp"

    # [2026-10-01 v1.1.0] Backend di llama.cpp: auto / cuda / rocm / vulkan / cpu.
    # "auto" sceglie in base al vendor della GPU rilevata (vedi installer.resolve_llama_backend).
    # Usato solo da engine_type=llama_cpp; vecchie configurazioni senza il campo -> "auto".
    llama_backend: str = "auto"

    # Percorso dell'eseguibile o comando del motore di inferenza
    #   llama_cpp: llama-server.exe / /usr/local/bin/llama-server
    #   vllm:      comando vllm (dopo l'installazione con pip di solito e' gia' nel PATH, si puo' lasciare vuoto per il default)
    engine_path: str = ""

    # [2026-10-02 v1.1.37] Motori llama-server aggiuntivi scelti dall'utente (es. fork RDNA4 compilato a mano): [{"name": ..., "path": ...}].
    # Compaiono nell'elenco dei motori, si possono mettere in uso e partecipano al confronto tra motori del tuning.
    extra_engines: list = field(default_factory=list)

    # Cartella dei modelli (dove stanno i .gguf)
    models_dir: str = ""

    # Porta di ascolto del servizio di inferenza
    service_port: int = 8080

    # Metadati
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Locale"

    def to_dict(self) -> dict:
        d = asdict(self)
        # Non si persiste la password in chiaro
        d["password"] = ""
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        # Scarta i campi che non appartengono al dataclass
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


# ==================== Persistenza ====================

def _ensure_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_targets() -> list[Target]:
    """Carica tutte le macchine target salvate"""
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Target.from_dict(t) for t in data.get("targets", [])]
    except Exception:
        return []


def save_targets(targets: list[Target]) -> None:
    """Salva la lista delle macchine target"""
    _ensure_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"targets": [t.to_dict() for t in targets]},
            f, ensure_ascii=False, indent=2,
        )


def get_target(target_id: str) -> Optional[Target]:
    """Restituisce una singola macchina target per id"""
    for t in load_targets():
        if t.id == target_id:
            return t
    return None


def _same_identity(a: Target, b: Target) -> bool:
    """[2026-10-01 v1.1.0] Due Target descrivono la stessa macchina?
    Rete di sicurezza per il bug "nuova entry invece di aggiornamento": se il client
    non invia l'id (es. form Impostazioni vuoto) ma la macchina e' la stessa, si aggiorna
    la entry esistente invece di duplicarla.
      - ssh:   stesso host + porta + utente
      - local: stesso nome (la macchina locale e' una sola per nome)"""
    if a.conn_type != b.conn_type:
        return False
    if a.conn_type == "ssh":
        return (a.host.strip().lower(), a.port, a.user) == (b.host.strip().lower(), b.port, b.user)
    return a.name.strip().lower() == b.name.strip().lower()


def upsert_target(target: Target, match_identity: bool = False) -> list[Target]:
    """Aggiunge o aggiorna una macchina target.

    [2026-10-01 v1.1.0] Corretto bug: il frontend inviava sempre il form senza `id`,
    quindi Target() generava un nuovo uuid e la entry veniva accodata a targets.json
    invece di aggiornare quella esistente.
    Ora: 1) match per id; 2) se match_identity=True (chiamata dall'API di salvataggio)
    e l'id non esiste, match per identita' (_same_identity) riusando l'id esistente.

    # Versione precedente (2026-10-01, sostituita):
    # targets = load_targets()
    # for i, t in enumerate(targets):
    #     if t.id == target.id:
    #         targets[i] = target
    #         break
    # else:
    #     targets.append(target)
    # save_targets(targets)
    # return targets
    """
    targets = load_targets()
    idx = next((i for i, t in enumerate(targets) if t.id == target.id), -1)
    if idx < 0 and match_identity:
        idx = next((i for i, t in enumerate(targets) if _same_identity(t, target)), -1)
        if idx >= 0:
            target.id = targets[idx].id  # mantiene l'id esistente (usato da running_models, ecc.)
    if idx >= 0:
        # La password non e' persistita: se il client non la invia, nulla da preservare.
        # [2026-10-02 v1.1.37] i motori personalizzati gia' registrati non si perdono se la nuova entry non li porta (match per identita')
        if not target.extra_engines and targets[idx].extra_engines:
            target.extra_engines = targets[idx].extra_engines
        targets[idx] = target
    else:
        targets.append(target)
    save_targets(targets)
    return targets


def delete_target(target_id: str) -> list[Target]:
    """Elimina una macchina target"""
    targets = [t for t in load_targets() if t.id != target_id]
    save_targets(targets)
    return targets
