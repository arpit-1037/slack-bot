"""Safe file editing primitives for repository modification workflows."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.utils.helpers import get_logger

log = get_logger(__name__)


class FileEditorError(RuntimeError):
    """Base error for safe file editing failures."""


class UnsafePathError(FileEditorError):
    """Raised when a requested path escapes the repository root."""


class BlindOverwriteError(FileEditorError):
    """Raised when a write would overwrite an existing file without a guard."""


class ConcurrentModificationError(FileEditorError):
    """Raised when a file changed after a patch was generated."""


class FileEncodingError(FileEditorError):
    """Raised when a file cannot be safely decoded as UTF-8 text."""


@dataclass(frozen=True)
class FileSnapshot:
    """Text content and safety metadata for one repository file."""

    path: str
    absolute_path: str
    content: str
    encoding: str
    newline: str
    checksum: str
    exists: bool = True


@dataclass(frozen=True)
class FileBackup:
    """Backup metadata used for rollback."""

    path: str
    backup_path: str | None
    checksum: str | None
    existed: bool


@dataclass(frozen=True)
class FileUpdate:
    """A guarded file update.

    Set ``content`` to ``None`` to delete the file. Existing files require
    ``expected_checksum`` so the editor can detect concurrent modifications.
    """

    path: str
    content: str | None
    expected_checksum: str | None = None
    encoding: str = "utf-8"


@dataclass(frozen=True)
class AppliedFileChange:
    """A completed filesystem change with enough data to roll it back."""

    path: str
    action: str
    backup: FileBackup
    checksum_before: str | None
    checksum_after: str | None


class SafeFileEditor:
    """Read, write, delete, backup, and rollback repository files safely."""

    def __init__(
        self,
        repo_root: str | os.PathLike[str] | None = None,
        backup_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else None
        default_backup_root = Path(tempfile.gettempdir()) / "slack-claude-bot-modification-backups"
        self.backup_root = Path(backup_root).resolve() if backup_root else default_backup_root

    def read_file(
        self,
        path: str,
        repo_root: str | os.PathLike[str] | None = None,
    ) -> FileSnapshot:
        """Read a UTF-8 text file and return content plus safety metadata."""
        absolute_path = self.resolve_path(path, repo_root=repo_root)
        if not absolute_path.exists():
            raise FileNotFoundError(f"Repository file not found: {path}")
        if not absolute_path.is_file():
            raise FileEditorError(f"Repository path is not a file: {path}")

        raw = absolute_path.read_bytes()
        content, encoding = self._decode_bytes(raw, path)
        snapshot = FileSnapshot(
            path=self.relative_path(absolute_path, repo_root=repo_root),
            absolute_path=str(absolute_path),
            content=content,
            encoding=encoding,
            newline=self._detect_newline(content),
            checksum=self._checksum(raw),
        )
        log.info("Safely read file path=%s bytes=%d", snapshot.path, len(raw))
        return snapshot

    def read_files(
        self,
        paths: Iterable[str],
        repo_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, FileSnapshot]:
        """Read multiple files and return snapshots keyed by repository path."""
        snapshots = {}
        for path in paths:
            snapshot = self.read_file(path, repo_root=repo_root)
            snapshots[snapshot.path] = snapshot
        return snapshots

    def write_file(
        self,
        path: str,
        content: str,
        expected_checksum: str | None = None,
        encoding: str = "utf-8",
        repo_root: str | os.PathLike[str] | None = None,
    ) -> AppliedFileChange:
        """Atomically write a file after checksum verification and backup creation."""
        absolute_path = self.resolve_path(path, repo_root=repo_root)
        relative_path = self.relative_path(absolute_path, repo_root=repo_root)
        existed = absolute_path.exists()
        checksum_before: str | None = None
        mode: int | None = None

        if existed:
            if expected_checksum is None:
                raise BlindOverwriteError(
                    f"Refusing to overwrite {relative_path} without an expected checksum."
                )
            raw_before = absolute_path.read_bytes()
            checksum_before = self._checksum(raw_before)
            if checksum_before != expected_checksum:
                raise ConcurrentModificationError(
                    f"Refusing to update {relative_path}: file changed after planning."
                )
            mode = absolute_path.stat().st_mode
        elif expected_checksum is not None:
            raise ConcurrentModificationError(
                f"Refusing to create {relative_path}: expected an existing file checksum."
            )

        backup = self.create_backup(relative_path, repo_root=repo_root)
        if existed and backup.checksum != expected_checksum:
            raise ConcurrentModificationError(
                f"Refusing to update {relative_path}: file changed during backup creation."
            )
        payload = self._encode_text(content, encoding, relative_path)
        self._atomic_write(absolute_path, payload, mode=mode)
        checksum_after = self._checksum(payload)
        log.info(
            "Safely wrote file path=%s action=%s bytes=%d backup=%s",
            relative_path,
            "update" if existed else "create",
            len(payload),
            backup.backup_path,
        )
        return AppliedFileChange(
            path=relative_path,
            action="update" if existed else "create",
            backup=backup,
            checksum_before=checksum_before,
            checksum_after=checksum_after,
        )

    def delete_file(
        self,
        path: str,
        expected_checksum: str | None,
        repo_root: str | os.PathLike[str] | None = None,
    ) -> AppliedFileChange:
        """Delete a file only after checksum verification and backup creation."""
        absolute_path = self.resolve_path(path, repo_root=repo_root)
        relative_path = self.relative_path(absolute_path, repo_root=repo_root)
        if not absolute_path.exists():
            raise FileNotFoundError(f"Repository file not found: {relative_path}")
        if expected_checksum is None:
            raise BlindOverwriteError(
                f"Refusing to delete {relative_path} without an expected checksum."
            )

        raw_before = absolute_path.read_bytes()
        checksum_before = self._checksum(raw_before)
        if checksum_before != expected_checksum:
            raise ConcurrentModificationError(
                f"Refusing to delete {relative_path}: file changed after planning."
            )

        backup = self.create_backup(relative_path, repo_root=repo_root)
        if backup.checksum != expected_checksum:
            raise ConcurrentModificationError(
                f"Refusing to delete {relative_path}: file changed during backup creation."
            )
        absolute_path.unlink()
        log.info("Safely deleted file path=%s backup=%s", relative_path, backup.backup_path)
        return AppliedFileChange(
            path=relative_path,
            action="delete",
            backup=backup,
            checksum_before=checksum_before,
            checksum_after=None,
        )

    def apply_file_changes(
        self,
        updates: Iterable[FileUpdate],
        repo_root: str | os.PathLike[str] | None = None,
    ) -> list[AppliedFileChange]:
        """Apply guarded updates transactionally, rolling back on the first failure."""
        applied: list[AppliedFileChange] = []
        try:
            for update in updates:
                if update.content is None:
                    applied.append(
                        self.delete_file(
                            update.path,
                            expected_checksum=update.expected_checksum,
                            repo_root=repo_root,
                        )
                    )
                else:
                    applied.append(
                        self.write_file(
                            update.path,
                            update.content,
                            expected_checksum=update.expected_checksum,
                            encoding=update.encoding,
                            repo_root=repo_root,
                        )
                    )
            return applied
        except Exception:
            log.exception("File update failed; rolling back applied changes=%d", len(applied))
            self.rollback(applied, repo_root=repo_root)
            raise

    def create_backup(
        self,
        path: str,
        repo_root: str | os.PathLike[str] | None = None,
    ) -> FileBackup:
        """Create a backup for an existing file and return rollback metadata."""
        absolute_path = self.resolve_path(path, repo_root=repo_root)
        relative_path = self.relative_path(absolute_path, repo_root=repo_root)
        if not absolute_path.exists():
            return FileBackup(path=relative_path, backup_path=None, checksum=None, existed=False)

        raw = absolute_path.read_bytes()
        backup_path = self._backup_path(relative_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(absolute_path, backup_path)
        log.info("Created file backup path=%s backup=%s", relative_path, backup_path)
        return FileBackup(
            path=relative_path,
            backup_path=str(backup_path),
            checksum=self._checksum(raw),
            existed=True,
        )

    def rollback(
        self,
        applied_changes: Iterable[AppliedFileChange],
        repo_root: str | os.PathLike[str] | None = None,
    ) -> None:
        """Rollback applied changes using their backups."""
        for change in reversed(list(applied_changes)):
            absolute_path = self.resolve_path(change.path, repo_root=repo_root)
            if change.backup.existed and change.backup.backup_path:
                absolute_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(change.backup.backup_path, absolute_path)
                log.info("Rolled back file path=%s from backup=%s", change.path, change.backup.backup_path)
            elif absolute_path.exists():
                absolute_path.unlink()
                log.info("Rolled back created file path=%s", change.path)

    def resolve_path(
        self,
        path: str,
        repo_root: str | os.PathLike[str] | None = None,
    ) -> Path:
        """Resolve a repository path while preventing path traversal."""
        root = self._repo_root(repo_root)
        candidate = Path(path)
        absolute_path = candidate if candidate.is_absolute() else root / candidate
        absolute_path = absolute_path.resolve(strict=False)

        if absolute_path != root and root not in absolute_path.parents:
            raise UnsafePathError(f"Path escapes repository root: {path}")
        return absolute_path

    def relative_path(
        self,
        absolute_path: str | os.PathLike[str],
        repo_root: str | os.PathLike[str] | None = None,
    ) -> str:
        """Return a stable POSIX-style repository-relative path."""
        root = self._repo_root(repo_root)
        return Path(absolute_path).resolve(strict=False).relative_to(root).as_posix()

    def _repo_root(self, repo_root: str | os.PathLike[str] | None = None) -> Path:
        root = Path(repo_root).resolve() if repo_root else self.repo_root
        if root is None:
            root = Path.cwd().resolve()
        return root

    def _backup_path(self, relative_path: str) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]
        safe_suffix = relative_path.replace("/", "__")
        return self.backup_root / timestamp / f"{unique}-{safe_suffix}"

    def _decode_bytes(self, raw: bytes, path: str) -> tuple[str, str]:
        if b"\x00" in raw:
            raise FileEncodingError(f"Refusing to edit binary-looking file: {path}")

        encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as error:
            raise FileEncodingError(f"Could not decode {path} as UTF-8 text.") from error

    def _encode_text(self, content: str, encoding: str, path: str) -> bytes:
        try:
            return content.encode(encoding)
        except UnicodeEncodeError as error:
            raise FileEncodingError(f"Could not encode {path} with {encoding}.") from error

    def _atomic_write(self, absolute_path: Path, payload: bytes, mode: int | None = None) -> None:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{absolute_path.name}.",
            suffix=".tmp",
            dir=str(absolute_path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                os.chmod(temp_name, mode)
            os.replace(temp_name, absolute_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _checksum(self, raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _detect_newline(self, content: str) -> str:
        if "\r\n" in content:
            return "\r\n"
        if "\r" in content:
            return "\r"
        return "\n"
