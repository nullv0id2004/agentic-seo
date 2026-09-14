"""Name -> analyst run function. Filled in as milestones add analysts."""
from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from analysts import content, ecommerce, keyword, offpage, onpage, report, technical, trend
from analysts.base import AnalystContext, AnalystInput

AnalystFn = Callable[[AnalystContext, AnalystInput], BaseModel]

ANALYSTS: dict[str, AnalystFn] = {
    "technical": technical.run,
    "trend": trend.run,
    "report": report.run,
    "keyword": keyword.run,
    "onpage": onpage.run,
    "content": content.run,
    "offpage": offpage.run,
    "ecommerce": ecommerce.run,
}
