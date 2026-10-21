"""Shared literal SKU grammar; no use-case imports or initialization cycle."""

import re

SKU_PATTERN = re.compile(r"(?<![\w-])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![\w-])")
