from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

SECRET_REF_PATTERN = re.compile(r"^provider-[0-9a-f-]{36}\.key$")


class LlmSecretError(Exception):
    pass


class LlmSecretStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _path(self, secret_ref: str) -> Path:
        if not SECRET_REF_PATTERN.fullmatch(secret_ref):
            raise LlmSecretError("LLM_SECRET_REF_INVALID")
        return self.directory / secret_ref

    def ref_for_provider(self, provider_id: str) -> str:
        secret_ref = f"provider-{provider_id}.key"
        self._path(secret_ref)
        return secret_ref

    def write_provider_credential(self, provider_id: str, credential: str) -> str:
        value = credential.strip()
        if not value or len(value) > 8192 or "\x00" in value:
            raise LlmSecretError("LLM_CREDENTIAL_INVALID")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        secret_ref = self.ref_for_provider(provider_id)
        target = self._path(secret_ref)
        descriptor, temporary = tempfile.mkstemp(prefix=".credential-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o400)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return secret_ref

    def read(self, secret_ref: str) -> str:
        try:
            value = self._path(secret_ref).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LlmSecretError("LLM_CREDENTIAL_UNREADABLE") from exc
        if not value:
            raise LlmSecretError("LLM_CREDENTIAL_UNREADABLE")
        return value

    def delete(self, secret_ref: str) -> None:
        try:
            self._path(secret_ref).unlink(missing_ok=True)
        except OSError as exc:
            raise LlmSecretError("LLM_CREDENTIAL_DELETE_FAILED") from exc
