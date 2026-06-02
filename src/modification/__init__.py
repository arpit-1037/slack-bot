"""Safe repository modification workflows."""

from src.modification.change_validator import ChangeValidator, ValidationIssue, ValidationResult
from src.modification.code_modifier import CodeModifier
from src.modification.diff_generator import DiffGenerator
from src.modification.diff_manager import DiffManager, FileDiffSummary
from src.modification.file_editor import FileUpdate, SafeFileEditor
from src.modification.modification_models import CodePatch, ModificationRequest, ModificationResult, PatchChange, SafetyIssue
from src.modification.patch_applier import AppliedPatch, PatchApplier
from src.modification.patch_generator import PatchGenerator, PatchOperation, PatchSet
from src.modification.repository_modifier import ModificationResult as RepositoryModificationResult
from src.modification.repository_modifier import RepositoryModifier
from src.modification.safety_guard import SafetyGuard, SafetyResult

__all__ = [
    "AppliedPatch",
    "ChangeValidator",
    "CodeModifier",
    "CodePatch",
    "DiffGenerator",
    "DiffManager",
    "FileDiffSummary",
    "FileUpdate",
    "ModificationResult",
    "ModificationRequest",
    "PatchApplier",
    "PatchChange",
    "PatchGenerator",
    "PatchOperation",
    "PatchSet",
    "RepositoryModificationResult",
    "RepositoryModifier",
    "SafeFileEditor",
    "SafetyGuard",
    "SafetyIssue",
    "SafetyResult",
    "ValidationIssue",
    "ValidationResult",
]
