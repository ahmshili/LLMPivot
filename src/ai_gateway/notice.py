"""Project attribution constants.

PivotLLM (LLMPivot) is shared under a source-available, view-and-evaluate
license -- it is NOT open source. See the LICENSE file at the repository
root for the full terms before you deploy, modify, or reference this
project anywhere.

This module exists so author/contact/repository information is defined
in exactly one place and then reused everywhere it needs to appear (API
metadata, HTTP response headers, the admin UI footer, CLI banner, etc.)
instead of being hand-copied -- keeping it consistent is also what keeps
it from silently drifting out of any one of those surfaces.

Per the LICENSE, this notice must be preserved wherever the project or
its UI is displayed, deployed for evaluation, or referenced.
"""

from __future__ import annotations

PROJECT_NAME = "PivotLLM"
AUTHOR_NAME = "ahmshili"
AUTHOR_EMAIL = "a.shili.pers@gmail.com"
GITHUB_URL = "https://github.com/ahmshili/LLMPivot"
GITLAB_URL = "https://gitlab.com/ahmshili"
ISSUES_URL = f"{GITHUB_URL}/issues"
LICENSE_URL = f"{GITHUB_URL}/blob/main/LICENSE"

ATTRIBUTION_LINE = (
    f"{PROJECT_NAME} by {AUTHOR_NAME} -- {GITHUB_URL} -- "
    f"portfolio project, source-available license (see LICENSE), not for "
    f"redistribution or production use without permission."
)

HTTP_HEADER_NAME = "X-Powered-By"
HTTP_HEADER_VALUE = f"{PROJECT_NAME} ({GITHUB_URL})"
