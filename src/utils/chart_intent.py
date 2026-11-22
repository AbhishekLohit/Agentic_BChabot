import re


_CHART_TERMS = r"(?:viz|vizualization|visuali[sz](?:e|ation)|chart|plot|graph)"
_EXPLICIT_CHART_REQUEST = re.compile(
    rf"\b(?:create|make|show|display|draw|build|generate)\b"
    rf".{{0,80}}\b{_CHART_TERMS}\b"
    rf"|\b(?:can you|could you)\s+(?:create|make|show|display|draw|build|generate|plot|visuali[sz]e)\b"
    rf".{{0,80}}\b{_CHART_TERMS}\b"
    rf"|\b(?:plot|graph|chart)\b.{{0,40}}\b(?:my|the|this|these|for me)\b"
    rf"|\b(?:want|need)\s+(?:to\s+)?(?:(?:see|have|create|make|show|generate)\s+)?"
    rf"(?:a|the|my|some)?\s*\b{_CHART_TERMS}\b"
    rf"|\b(?:see|view)\s+(?:a|the|my|some)\s+\b{_CHART_TERMS}\b"
    rf"|\b(?:plot|visuali[sz]e)\b(?:\s+\w+){{0,12}}"
    rf"|\b{_CHART_TERMS}\b\s+(?:please|for me)\b",
    re.IGNORECASE,
)
_EXPLICIT_PIE_REQUEST = re.compile(
    r"\bpie\s+(?:chart|graph)\b"
    r"|\b(?:chart|graph)\s+(?:type\s+)?(?:a\s+)?pie\b",
    re.IGNORECASE,
)


def is_explicit_chart_request(query: str | None) -> bool:
    return bool(query and _EXPLICIT_CHART_REQUEST.search(query))


def is_explicit_pie_request(query: str | None) -> bool:
    return bool(
        query
        and is_explicit_chart_request(query)
        and _EXPLICIT_PIE_REQUEST.search(query)
    )
