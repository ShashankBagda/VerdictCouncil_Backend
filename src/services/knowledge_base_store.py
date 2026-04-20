from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile

from src.shared.config import settings


def _storage_root() -> Path:
    return Path(settings.knowledge_base_storage_dir)


def _files_dir() -> Path:
    return _storage_root() / "files"


def _manifest_path() -> Path:
    return _storage_root() / "manifest.json"


def _read_manifest() -> dict:
    path = _manifest_path()
    if not path.exists():
        return {"initialized": False, "documents": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(manifest: dict) -> None:
    root = _storage_root()
    root.mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def initialize_store() -> dict:
    _files_dir().mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    manifest["initialized"] = True
    manifest.setdefault("documents", [])
    _write_manifest(manifest)
    return manifest


def list_documents() -> dict:
    manifest = _read_manifest()
    return {
        "initialized": bool(manifest.get("initialized", False)),
        "items": manifest.get("documents", []),
        "total": len(manifest.get("documents", [])),
    }


async def save_document(file: UploadFile) -> dict:
    manifest = _read_manifest()
    if not manifest.get("initialized"):
        manifest = initialize_store()

    file_id = str(uuid.uuid4())
    ext = Path(file.filename or "document").suffix
    stored_name = f"{file_id}{ext}"
    destination = _files_dir() / stored_name

    data = await file.read()
    destination.write_bytes(data)

    preview = ""
    if data:
        try:
            preview = data.decode("utf-8", errors="ignore")[:2000]
        except Exception:
            preview = ""

    doc = {
        "file_id": file_id,
        "filename": file.filename or stored_name,
        "stored_name": stored_name,
        "content_type": file.content_type or "application/octet-stream",
        "size_bytes": len(data),
        "uploaded_at": datetime.now(UTC).isoformat(),
        "preview": preview,
    }

    manifest.setdefault("documents", []).append(doc)
    manifest["initialized"] = True
    _write_manifest(manifest)
    return doc


def delete_document(file_id: str) -> bool:
    manifest = _read_manifest()
    docs = manifest.get("documents", [])
    target = next((d for d in docs if d.get("file_id") == file_id), None)
    if not target:
        return False

    stored_name = target.get("stored_name")
    if stored_name:
        path = _files_dir() / stored_name
        if path.exists():
            path.unlink()

    manifest["documents"] = [d for d in docs if d.get("file_id") != file_id]
    _write_manifest(manifest)
    return True


def search_documents(query: str, limit: int = 10) -> dict:
    manifest = _read_manifest()
    normalized = query.strip().lower()
    if not normalized:
        return {"items": [], "total": 0}

    matches: list[dict] = []
    for doc in manifest.get("documents", []):
        haystack = f"{doc.get('filename', '')} {doc.get('preview', '')}".lower()
        if normalized in haystack:
            frequency = haystack.count(normalized)
            score = min(1.0, 0.4 + (0.15 * frequency))
            matches.append(
                {
                    "file_id": doc.get("file_id"),
                    "filename": doc.get("filename"),
                    "content": (doc.get("preview") or "")[:400],
                    "score": round(score, 3),
                }
            )

    matches.sort(key=lambda item: item["score"], reverse=True)
    sliced = matches[:limit]
    return {"items": sliced, "total": len(sliced)}
