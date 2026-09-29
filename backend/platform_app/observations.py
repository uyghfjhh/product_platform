"""中立的观测记录类型与通用解析工具。

平台层只持有产品无关的部分：``ParsedObservation`` 是产品与平台之间
传递观测结果的通用记录；``parse_tsv_rows`` 是通用的无对齐 TSV 表格
解析器。具体的指标语义（如 ``postgres.replication``）由产品侧实现，
见 ``products/pg_common/`` 与各产品包的 ``observations.py``。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedObservation:
    kind: str
    state: str
    details: dict[str, str]


def parse_tsv_rows(text: str) -> list[dict[str, str]]:
    """Parse unaligned psql rows while ignoring incomplete lines."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [item.strip() for item in lines[0].split("\t")]
    rows = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == len(headers):
            rows.append(dict(zip(headers, (value.strip() for value in values))))
    return rows
