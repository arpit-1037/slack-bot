"""Safe repository modification workflows."""

from src.modification.change_validator import ChangeValidator, ValidationIssue, ValidationResult
from src.modification.diff_manager import DiffManager, FileDiffSummary
from src.modification.file_editor import FileUpdate, SafeFileEditor
from src.modification.patch_generator import PatchGenerator, PatchOperation, PatchSet
from src.modification.repository_modifier import ModificationResult, RepositoryModifier

__all__ = [
    "ChangeValidator",
    "DiffManager",
    "FileDiffSummary",
    "FileUpdate",
    "ModificationResult",
    "PatchGenerator",
    "PatchOperation",
    "PatchSet",
    "RepositoryModifier",
    "SafeFileEditor",
    "ValidationIssue",
    "ValidationResult",
]
