"""Fila persistente de downloads. Sem chaves API e fora da pasta distribuída."""
import json
import os
from pathlib import Path

SCHEMA = 1
VALID_STATES = {"queued", "running", "interrupted", "done", "error"}


def save_queue(path, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Nunca persistir segredos API, objectos de resposta ou campos de controlo efémeros.
    allowed = ("title", "source", "status", "message", "filename", "row", "folder", "attempts")
    data = {"version": SCHEMA, "downloads": [
        {k: entry[k] for k in allowed if k in entry} for entry in entries
    ]}
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as f:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def load_queue(path):
    path = Path(path)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(saved, dict) or saved.get("version") != SCHEMA:
        raise ValueError("Formato da fila de downloads desconhecido")
    downloads = saved.get("downloads")
    if not isinstance(downloads, list):
        raise ValueError("Fila de downloads inválida")
    recovered = []
    for item in downloads[:10000]:
        if not isinstance(item, dict) or not isinstance(item.get("row"), dict):
            continue
        row = item["row"]
        if not isinstance(row.get("title"), str) or not row.get("title") or not isinstance(item.get("folder"), str):
            continue
        status = item.get("status", "interrupted")
        if status not in VALID_STATES:
            status = "interrupted"
        if status == "running":
            status = "interrupted"
            message = "Interrompido ao fechar a aplicação. Carrega em Retomar pendentes."
        elif status == "queued":
            message = "Pendente da sessão anterior. Carrega em Retomar pendentes."
        else:
            message = str(item.get("message", ""))[:1500]
        recovered.append({
            "title": row["title"], "source": str(item.get("source", "Por verificar")),
            "status": status, "message": message, "row": row,
            "folder": item["folder"], "attempts": int(item.get("attempts", 0)),
            **({"filename": str(item["filename"])} if item.get("filename") else {}),
        })
    return recovered
