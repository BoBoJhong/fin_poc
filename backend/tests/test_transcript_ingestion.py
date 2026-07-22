from scripts.ingest_transcripts import (
    TranscriptSource,
    chunk_turns,
    seed_neo4j,
    remove_legacy_speaker_graph,
    split_plain_text_turns,
    split_speaker_turns,
    transcript_turns,
)


def test_transcript_adapter_preserves_speaker_and_qa_section() -> None:
    lines = [
        "navigation",
        "JONATHAN NEILSON:",
        "Welcome to the call. This is a prepared statement.",
        "SATYA NADELLA: We added capacity for customer demand.",
        "JONATHAN NEILSON: We’ll now move over to Q&A.",
    ]
    for index in range(8):
        lines.append(f"ANALYST {index}: What is the outlook for cloud demand?")
        lines.append("AMY HOOD: Demand continues to exceed available supply.")
    turns = split_speaker_turns("\n".join(lines))
    assert turns[0]["speaker"] == "JONATHAN NEILSON"
    assert turns[1]["speaker"] == "SATYA NADELLA"
    assert any(turn["section"] == "question_and_answer" for turn in turns)
    chunks = chunk_turns(turns, max_chars=120)
    assert all(chunk["speaker"] for chunk in chunks)
    assert all(chunk["paragraph_id"].startswith("turn-") for chunk in chunks)


def test_plain_text_adapter_accepts_common_speaker_layouts() -> None:
    text = """王小明：第一段內容
王小明: 第二段內容
[王小明] 第三段內容
王小明（執行長）：第四段內容
Speaker: 王小明
Title: 執行長
Content: 第五段內容
"""

    turns = split_plain_text_turns(text)

    assert [turn["text"] for turn in turns] == [
        "第一段內容",
        "第二段內容",
        "第三段內容",
        "第四段內容",
        "第五段內容",
    ]
    assert all(turn["speaker"] == "王小明" for turn in turns)
    assert all(turn["title"] == "執行長" for turn in turns)
    assert all(turn["section"] == "unknown" for turn in turns)
    assert transcript_turns(text.encode(), "plain_text") == turns


def test_plain_text_adapter_only_sets_section_from_explicit_heading() -> None:
    turns = split_plain_text_turns(
        """Prepared Remarks
CEO: Opening statement.
Q&A
[Analyst] What is the outlook?
CEO: Demand remains strong.
"""
    )

    assert [turn["section"] for turn in turns] == [
        "prepared_remarks",
        "question_and_answer",
        "question_and_answer",
    ]


def test_transcript_chunking_merges_short_turns_and_hard_splits_long_turns() -> None:
    turns = [
        {
            "speaker": "ANALYST",
            "section": "question_and_answer",
            "text": "Could you explain capacity?",
        },
        {
            "speaker": "CEO",
            "section": "question_and_answer",
            "text": " ".join(f"capacity-{index}" for index in range(80)),
        },
        {
            "speaker": "ANALYST",
            "section": "question_and_answer",
            "text": "Thank you.",
        },
    ]

    chunks = chunk_turns(turns, max_chars=300, min_chars=80)

    assert chunks
    assert all(len(chunk["text"]) <= 300 for chunk in chunks)
    assert all(chunk["sequence"] == index for index, chunk in enumerate(chunks, start=1))
    assert any(len(chunk["speakers"]) > 1 for chunk in chunks)
    combined = " ".join(chunk["text"] for chunk in chunks)
    assert "Could you explain capacity?" in combined
    assert "capacity-79" in combined
    assert "Thank you." in combined


def test_transcript_reingestion_removes_stale_chunks() -> None:
    class RecordingDriver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute_query(self, query: str, **parameters) -> None:
            self.calls.append((query, parameters))

    driver = RecordingDriver()
    source = TranscriptSource(
        source_key="test-call",
        co_code="TEST",
        company_name="Test Corp",
        period="2026Q1",
        fiscal_label="FY2026 Q1",
        event_date="2026-04-30",
        title="Test earnings call",
        url="https://example.test/call",
        adapter="test",
    )
    chunks = chunk_turns(
        [{"speaker": "CEO", "section": "prepared_remarks", "text": "Material outlook."}]
    )
    seed_neo4j(driver, source, b"raw", chunks, [[0.1, 0.2]], "neo4j")

    stale_call = next(
        call for call in driver.calls if "DETACH DELETE stale" in call[0] and "chunk_ids" in call[1]
    )
    assert stale_call[1]["chunk_ids"] == ["ir-test-call-transcript-turn-001-to-001-block-001"]
    graph_queries = "\n".join(call[0] for call in driver.calls)
    assert "document:EarningsCall" in graph_queries
    assert "HAS_EARNINGS_CALL" in graph_queries
    assert "HAS_TURN" in graph_queries
    assert "CONTAINS_TURN" in graph_queries
    assert "HAS_PARTICIPANT" not in graph_queries
    assert "SPOKEN_BY" not in graph_queries
    turn_call = next(call for call in driver.calls if "MERGE (turn:SpeakerTurn" in call[0])
    assert turn_call[1]["turns"][0]["speaker"] == "CEO"
    assert turn_call[1]["turns"][0]["speaker_title"] is None


def test_legacy_speaker_graph_cleanup_is_explicit() -> None:
    class RecordingDriver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute_query(self, query: str, **parameters) -> None:
            self.calls.append((query, parameters))

    driver = RecordingDriver()
    remove_legacy_speaker_graph(driver, "neo4j")

    queries = "\n".join(query for query, _ in driver.calls)
    assert "HAS_PARTICIPANT" in queries
    assert "SPOKEN_BY" in queries
    assert "DETACH DELETE speaker" in queries
