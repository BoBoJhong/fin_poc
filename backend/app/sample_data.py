from __future__ import annotations

from app.models import Evidence, SourceLocator, SourcePreview, SourceType


COMPANIES = {
    "DEMO01": {
        "name": "範例科技股份有限公司",
        "industry": "企業軟體",
        "aliases": ["範例科技", "範科"],
    },
    "DEMO02": {
        "name": "示範製造股份有限公司",
        "industry": "智慧製造",
        "aliases": ["示範製造", "示製"],
    },
}


EVIDENCE: list[Evidence] = [
    Evidence(
        evidence_id="ev-demo01-metric-revenue",
        co_code="DEMO01",
        source_id="demo01-financial-metrics-2026q2",
        source_type=SourceType.DATABASE,
        title="範例科技 2026 Q2 財務指標（虛構）",
        content="2026 Q2 單季合併營收為新台幣 128.4 億元。",
        score=1.0,
        period="2026Q2",
        locator=SourceLocator(
            table="financial_metrics",
            primary_key="DEMO01|2026Q2|revenue",
            columns=["co_code", "period", "metric_code", "value", "unit"],
        ),
        metadata={
            "metric_code": "revenue",
            "value": 128.4,
            "unit": "TWD_100M",
            "scope": "consolidated_quarter",
            "is_synthetic": True,
        },
    ),
    Evidence(
        evidence_id="ev-demo01-metric-gm",
        co_code="DEMO01",
        source_id="demo01-financial-metrics-2026q2",
        source_type=SourceType.DATABASE,
        title="範例科技 2026 Q2 財務指標（虛構）",
        content="2026 Q2 單季合併毛利率為 42.1%。",
        score=1.0,
        period="2026Q2",
        locator=SourceLocator(
            table="financial_metrics",
            primary_key="DEMO01|2026Q2|gross_margin",
            columns=["co_code", "period", "metric_code", "value", "unit"],
        ),
        metadata={
            "metric_code": "gross_margin",
            "value": 42.1,
            "unit": "PERCENT",
            "scope": "consolidated_quarter",
            "is_synthetic": True,
        },
    ),
    Evidence(
        evidence_id="ev-demo02-metric-revenue",
        co_code="DEMO02",
        source_id="demo02-financial-metrics-2026q2",
        source_type=SourceType.DATABASE,
        title="示範製造 2026 Q2 財務指標（虛構）",
        content="2026 Q2 單季合併營收為新台幣 76.2 億元。",
        score=1.0,
        period="2026Q2",
        locator=SourceLocator(
            table="financial_metrics",
            primary_key="DEMO02|2026Q2|revenue",
            columns=["co_code", "period", "metric_code", "value", "unit"],
        ),
        metadata={
            "metric_code": "revenue",
            "value": 76.2,
            "unit": "TWD_100M",
            "scope": "consolidated_quarter",
            "is_synthetic": True,
        },
    ),
    Evidence(
        evidence_id="ev-demo01-call-risk",
        co_code="DEMO01",
        source_id="demo01-2026q2-call",
        source_type=SourceType.TRANSCRIPT,
        title="範例科技 2026 Q2 法說會逐字稿（虛構）",
        content=(
            "財務長表示，下半年主要不確定性包括海外專案驗收遞延、匯率波動，"
            "以及雲端基礎設施成本上升；公司尚未因此調整全年展望。"
        ),
        score=0.94,
        period="2026Q2",
        locator=SourceLocator(paragraph_id="p-18", timestamp="00:12:31"),
        captured_at="2026-07-10T10:00:00+08:00",
        content_hash="sha256:demo01-call-p18-v1",
        metadata={"speaker": "財務長", "is_synthetic": True},
    ),
    Evidence(
        evidence_id="ev-demo01-graph-product",
        co_code="DEMO01",
        source_id="demo01-2026q2-call",
        source_type=SourceType.GRAPH,
        title="範例科技產品與風險關聯（虛構）",
        content=(
            "圖譜路徑顯示：範例科技 -[SELLS]-> Atlas ERP -[EXPOSED_TO]-> 海外專案驗收遞延；"
            "此關聯源自 2026 Q2 法說會第 18 段。"
        ),
        score=0.88,
        period="2026Q2",
        locator=SourceLocator(
            graph_path=[
                "Company:DEMO01",
                "SELLS",
                "Product:Atlas ERP",
                "EXPOSED_TO",
                "Risk:海外專案驗收遞延",
            ]
        ),
        metadata={
            "hops": 2,
            "is_synthetic": True,
            "relationship_provenance": [
                {
                    "type": "MENTIONS",
                    "co_code": "DEMO01",
                    "source_id": "demo01-2026q2-call",
                    "period": "2026Q2",
                    "data_version": "demo-v1",
                },
                {
                    "type": "EXPOSED_TO",
                    "co_code": "DEMO01",
                    "source_id": "demo01-2026q2-call",
                    "period": "2026Q2",
                    "data_version": "demo-v1",
                },
                {
                    "type": "SELLS",
                    "co_code": "DEMO01",
                    "source_id": "demo01-2026q2-call",
                    "period": "2026Q2",
                    "data_version": "demo-v1",
                },
            ],
        },
    ),
]


SOURCE_PREVIEWS: dict[str, SourcePreview] = {
    "demo01-financial-metrics-2026q2": SourcePreview(
        source_id="demo01-financial-metrics-2026q2",
        co_code="DEMO01",
        source_type=SourceType.DATABASE,
        title="範例科技 2026 Q2 財務指標（虛構）",
        database_record={
            "table": "financial_metrics",
            "records": [
                {
                    "co_code": "DEMO01",
                    "period": "2026Q2",
                    "metric_code": "revenue",
                    "value": 128.4,
                    "unit": "TWD_100M",
                },
                {
                    "co_code": "DEMO01",
                    "period": "2026Q2",
                    "metric_code": "gross_margin",
                    "value": 42.1,
                    "unit": "PERCENT",
                },
            ],
            "data_version": "demo-v1",
            "is_synthetic": True,
        },
    ),
    "demo01-2026q2-call": SourcePreview(
        source_id="demo01-2026q2-call",
        co_code="DEMO01",
        source_type=SourceType.TRANSCRIPT,
        title="範例科技 2026 Q2 法說會逐字稿（虛構）",
        text=(
            "[00:12:31] 財務長：下半年主要不確定性包括海外專案驗收遞延、"
            "匯率波動，以及雲端基礎設施成本上升；公司尚未因此調整全年展望。"
        ),
        locator=SourceLocator(paragraph_id="p-18", timestamp="00:12:31"),
        captured_at="2026-07-10T10:00:00+08:00",
        content_hash="sha256:demo01-call-p18-v1",
    ),
    "demo02-financial-metrics-2026q2": SourcePreview(
        source_id="demo02-financial-metrics-2026q2",
        co_code="DEMO02",
        source_type=SourceType.DATABASE,
        title="示範製造 2026 Q2 財務指標（虛構）",
        database_record={
            "table": "financial_metrics",
            "records": [
                {
                    "co_code": "DEMO02",
                    "period": "2026Q2",
                    "metric_code": "revenue",
                    "value": 76.2,
                    "unit": "TWD_100M",
                }
            ],
            "data_version": "demo-v1",
            "is_synthetic": True,
        },
    ),
    "demo01-2026q2-report": SourcePreview(
        source_id="demo01-2026q2-report",
        co_code="DEMO01",
        source_type=SourceType.URL,
        title="範例科技 2026 Q2 投資人報告頁面快照（虛構）",
        snapshot_html=(
            "<!doctype html><html lang='zh-Hant'><body style='font-family:sans-serif;padding:24px'>"
            "<h2>範例科技 2026 Q2 投資人報告</h2>"
            "<p><strong>注意：</strong>此頁為 PoC 虛構快照。</p>"
            "<h3 id='risk'>主要風險</h3><p>海外專案驗收遞延、匯率波動與雲端成本。</p>"
            "</body></html>"
        ),
        live_url="https://example.com/investor/demo01/2026q2",
        locator=SourceLocator(paragraph_id="risk"),
        captured_at="2026-07-10T10:00:00+08:00",
        content_hash="sha256:demo01-report-v1",
    ),
}


def company_name(co_code: str) -> str:
    return COMPANIES.get(co_code.upper(), {}).get("name", co_code.upper())
