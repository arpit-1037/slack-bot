"""Static text fragments for execution findings reports."""

EXECUTION_TITLE = "*Execution Engine*"
READ_ONLY_NOTICE = "Read-only investigation completed with registered safe tools only."
VALIDATION_FAILED_NOTICE = "Read-only execution was not started because validation failed."

REPORT_SECTIONS = {
    "summary": "*Investigation Summary:*",
    "tools": "*Tools Executed:*",
    "files": "*Files Examined:*",
    "commits": "*Commits Reviewed:*",
    "issues": "*Issues Found:*",
    "recommendations": "*Recommendations:*",
    "failures": "*Failures:*",
}
