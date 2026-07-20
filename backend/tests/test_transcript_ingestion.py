from scripts.ingest_transcripts import chunk_turns, split_speaker_turns


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
