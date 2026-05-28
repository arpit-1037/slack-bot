"""Generic terminal command adapter for future non-git tools."""

from __future__ import annotations

import subprocess


class TerminalTool:
    """Run explicit command argument lists without invoking a shell."""

    def run(self, args: list[str], timeout: int = 120, cwd: str | None = None) -> tuple[bool, str]:
        """Execute a command and return success plus combined output."""
        try:
            result = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return False, "Command timed out."
        except Exception as error:
            return False, str(error)

        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return result.returncode == 0, output or "Command completed."
