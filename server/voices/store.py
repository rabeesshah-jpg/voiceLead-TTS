"""On-disk voice profile index and per-profile metadata."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schemas import VoiceProfileMetadata

logger = logging.getLogger(__name__)


class VoiceProfileStore:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.index_path = storage_dir / "index.json"
        self._profiles: dict[str, VoiceProfileMetadata] = {}

    def load(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._profiles = {}
            self._persist_index()
            return
        with self.index_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        profiles: dict[str, VoiceProfileMetadata] = {}
        for voice_id, item in raw.get("voices", {}).items():
            if not item.get("provider_voice_id") and item.get("engine_voice_name"):
                item["provider_voice_id"] = item["engine_voice_name"]
            if not item.get("updated_at"):
                item["updated_at"] = item.get("created_at")
            profiles[voice_id] = VoiceProfileMetadata.model_validate(item)
        self._profiles = profiles

    def _persist_index(self) -> None:
        payload = {
            "voices": {
                voice_id: meta.model_dump()
                for voice_id, meta in sorted(self._profiles.items())
            }
        }
        tmp = self.index_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.index_path)

    def list_profiles(self) -> list[VoiceProfileMetadata]:
        return sorted(self._profiles.values(), key=lambda p: p.created_at, reverse=True)

    def get(self, voice_id: str) -> Optional[VoiceProfileMetadata]:
        return self._profiles.get(voice_id)

    def profile_dir(self, voice_id: str) -> Path:
        return self.storage_dir / voice_id

    def new_voice_id(self) -> str:
        return str(uuid.uuid4())

    def upsert(self, metadata: VoiceProfileMetadata) -> VoiceProfileMetadata:
        now = self.now_iso()
        if metadata.updated_at is None:
            metadata.updated_at = now
        else:
            metadata.updated_at = now
        self._profiles[metadata.voice_id] = metadata
        profile_dir = self.profile_dir(metadata.voice_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        meta_path = profile_dir / "metadata.json"
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(), f, indent=2)
        self._persist_index()
        return metadata

    def delete(self, voice_id: str) -> Optional[VoiceProfileMetadata]:
        meta = self._profiles.pop(voice_id, None)
        if meta is None:
            return None
        profile_dir = self.profile_dir(voice_id)
        if profile_dir.exists() and profile_dir.is_dir():
            # Safety: only delete inside configured storage root.
            resolved = profile_dir.resolve()
            root = self.storage_dir.resolve()
            if resolved.is_relative_to(root):
                shutil.rmtree(profile_dir)
            else:
                logger.error("Refusing to delete profile outside storage dir: %s", profile_dir)
        self._persist_index()
        return meta

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
