from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

import httpx
from neo4j import GraphDatabase

from app.config import PROJECT_ROOT, get_settings
from scripts.ingest_sec import html_to_text
from scripts.init_data import create_indexes, embed, sha256
from scripts.text_blocks import build_semantic_blocks


@dataclass(frozen=True, slots=True)
class TranscriptSource:
    source_key: str
    co_code: str
    company_name: str
    period: str
    fiscal_label: str
    event_date: str
    title: str
    url: str
    adapter: str
    material_kind: str = "full_transcript"
    speaker_titles: dict[str, str] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return f"ir-{self.source_key}-transcript"

    @property
    def data_version(self) -> str:
        return f"ir:{self.co_code.lower()}:{self.event_date}"


SOURCES = {
    "msft-fy2026-q3": TranscriptSource(
        source_key="msft-fy2026-q3",
        co_code="MSFT",
        company_name="Microsoft Corporation",
        period="2026Q1",
        fiscal_label="FY2026 Q3",
        event_date="2026-04-29",
        title="Microsoft FY2026 Q3 Earnings Conference Call Transcript",
        url="https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q3",
        adapter="microsoft_ir_html",
        speaker_titles={
            "SATYA NADELLA": "Chairman and CEO",
            "AMY HOOD": "EVP & CFO",
        },
    ),
    "msft-fy2026-q2": TranscriptSource(
        source_key="msft-fy2026-q2",
        co_code="MSFT",
        company_name="Microsoft Corporation",
        period="2025Q4",
        fiscal_label="FY2026 Q2",
        event_date="2026-01-28",
        title="Microsoft FY2026 Q2 Earnings Conference Call Transcript",
        url="https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q2",
        adapter="microsoft_ir_html",
        speaker_titles={
            "SATYA NADELLA": "Chairman and CEO",
            "AMY HOOD": "EVP & CFO",
        },
    ),
}

SPEAKER_PATTERN = re.compile(r"^([A-Z][A-Z .'-]+(?:, [A-Za-z .&'/-]+)?):\s*(.*)$")
PLAIN_FIELD_PATTERN = re.compile(
    r"^(Speaker|Title|Content|發言人|職稱|內容)\s*[：:]\s*(.*)$",
    re.IGNORECASE,
)
PLAIN_TITLED_SPEAKER_PATTERN = re.compile(r"^(.{1,80}?)[（(]([^）)]{1,80})[）)]\s*[：:]\s*(.*)$")
PLAIN_BRACKETED_SPEAKER_PATTERN = re.compile(r"^\[([^]\r\n]{1,80})\]\s*(.*)$")
PLAIN_SPEAKER_PATTERN = re.compile(r"^([^：:\r\n]{1,80})\s*[：:]\s*(.*)$")
QA_SECTION_PATTERN = re.compile(
    r"^(?:Q\s*&\s*A|Questions?\s+(?:and|&)\s+Answers?|問答(?:環節|階段)?)$",
    re.IGNORECASE,
)
PREPARED_SECTION_PATTERN = re.compile(
    r"^(?:Prepared Remarks?|Management Remarks?|管理層(?:說明|致詞)|開場(?:說明|致詞))$",
    re.IGNORECASE,
)


def split_speaker_turns(text: str) -> list[dict[str, str]]:
    """Normalize transcript layout into speaker turns without relying on HTML classes."""
    turns: list[dict[str, str]] = []
    speaker: str | None = None
    section = "prepared_remarks"
    content: list[str] = []

    def flush() -> None:
        if speaker and content:
            normalized = re.sub(r"\s+", " ", " ".join(content)).strip()
            if normalized:
                turns.append({"speaker": speaker, "section": section, "text": normalized})

    for line in (value.strip() for value in text.splitlines() if value.strip()):
        if (
            speaker
            and len(turns) >= 10
            and re.match(
                r"^END$|^(?:END\s+)?Microsoft Corp \(MSFT\)|^\d{4} ANNUAL REPORT$",
                line,
                re.IGNORECASE,
            )
        ):
            break
        match = SPEAKER_PATTERN.match(line)
        if not match:
            if speaker:
                content.append(line)
            continue
        flush()
        label, initial = match.groups()
        speaker = label.strip()
        content = [initial] if initial else []
        if re.search(r"(?:move over to|go to|begin) Q&A|Q&A portion", line, re.IGNORECASE):
            section = "question_and_answer"
    flush()
    if len(turns) < 10:
        raise ValueError("Transcript adapter found too few speaker turns")
    return turns


def _is_plain_speaker_label(value: str) -> bool:
    label = value.strip()
    if not label or len(label) > 80 or re.search(r"[。！？!?；;]", label):
        return False
    if re.search(r"[\u3400-\u9fff]", label):
        return len(label) <= 40
    if label.casefold() in {"operator", "analyst", "moderator", "host"}:
        return True
    words = label.split()
    return label == label.upper() or (
        len(words) >= 2 and all(word[:1].isupper() for word in words if word)
    )


def split_plain_text_turns(text: str) -> list[dict[str, str]]:
    """Normalize common plain-text transcript layouts into ordered speaker turns."""
    turns: list[dict[str, str]] = []
    speaker: str | None = None
    title: str | None = None
    section = "unknown"
    content: list[str] = []

    def flush() -> None:
        nonlocal content
        if speaker and content:
            normalized = re.sub(r"\s+", " ", " ".join(content)).strip()
            if normalized:
                turn = {"speaker": speaker, "section": section, "text": normalized}
                if title:
                    turn["title"] = title
                turns.append(turn)
        content = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if QA_SECTION_PATTERN.fullmatch(line):
            flush()
            speaker = None
            title = None
            section = "question_and_answer"
            continue
        if PREPARED_SECTION_PATTERN.fullmatch(line):
            flush()
            speaker = None
            title = None
            section = "prepared_remarks"
            continue

        field_match = PLAIN_FIELD_PATTERN.match(line)
        if field_match:
            field_name, value = field_match.groups()
            field_name = field_name.casefold()
            value = value.strip()
            if field_name in {"speaker", "發言人"}:
                flush()
                speaker = value
                title = None
            elif field_name in {"title", "職稱"}:
                if speaker:
                    title = value or None
            elif speaker and value:
                content.append(value)
            continue

        titled_match = PLAIN_TITLED_SPEAKER_PATTERN.match(line)
        if titled_match and _is_plain_speaker_label(titled_match.group(1)):
            flush()
            speaker, title, initial = (value.strip() for value in titled_match.groups())
            content = [initial] if initial else []
            continue

        bracketed_match = PLAIN_BRACKETED_SPEAKER_PATTERN.match(line)
        if bracketed_match and _is_plain_speaker_label(bracketed_match.group(1)):
            flush()
            speaker, initial = (value.strip() for value in bracketed_match.groups())
            title = None
            content = [initial] if initial else []
            continue

        speaker_match = PLAIN_SPEAKER_PATTERN.match(line)
        if speaker_match and _is_plain_speaker_label(speaker_match.group(1)):
            flush()
            speaker, initial = (value.strip() for value in speaker_match.groups())
            title = None
            content = [initial] if initial else []
            continue

        if speaker:
            content.append(line)

    flush()
    if not turns:
        raise ValueError("Plain-text transcript adapter found no speaker turns")

    known_titles = {turn["speaker"]: turn["title"] for turn in turns if turn.get("title")}
    for turn in turns:
        known_title = known_titles.get(turn["speaker"])
        if known_title:
            turn["title"] = known_title
    return turns


def _merge_turn_chunks(items: list[dict[str, Any]]) -> dict[str, Any]:
    speakers = list(dict.fromkeys(speaker for item in items for speaker in item["speakers"]))
    primary = max(items, key=lambda item: item["body_chars"])["speaker"]
    return {
        "speaker": primary,
        "speakers": speakers,
        "section": items[0]["section"],
        "turn_start": items[0]["turn_start"],
        "turn_end": items[-1]["turn_end"],
        "body_chars": sum(item["body_chars"] for item in items),
        "text": "\n\n".join(item["text"] for item in items),
    }


def _merge_short_turns(
    chunks: list[dict[str, Any]], max_chars: int, min_chars: int
) -> list[dict[str, Any]]:
    merged = list(chunks)
    changed = True
    while changed:
        changed = False
        for index, item in enumerate(merged):
            if item["body_chars"] >= min_chars:
                continue
            candidates = []
            for neighbor in (index - 1, index + 1):
                if not 0 <= neighbor < len(merged):
                    continue
                if merged[neighbor]["section"] != item["section"]:
                    continue
                start, end = sorted((index, neighbor))
                combined = _merge_turn_chunks(merged[start : end + 1])
                if len(combined["text"]) <= max_chars:
                    candidates.append((neighbor, combined))
            if not candidates:
                continue
            neighbor, combined = max(
                candidates,
                key=lambda candidate: max_chars - len(candidate[1]["text"]),
            )
            start, end = sorted((index, neighbor))
            merged[start : end + 1] = [combined]
            changed = True
            break
    return merged


def chunk_turns(
    turns: list[dict[str, str]], max_chars: int = 1_400, min_chars: int = 160
) -> list[dict[str, Any]]:
    effective_min = min(min_chars, max_chars // 2)
    raw_chunks: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(turns, start=1):
        prefix = f"Speaker: {turn['speaker']}\nSection: {turn['section']}\n"
        body_limit = max_chars - len(prefix)
        if body_limit < 32:
            raise ValueError("max_chars is too small for transcript metadata")
        parts = build_semantic_blocks(
            [turn["text"]],
            max_chars=body_limit,
            min_chars=min(effective_min, body_limit // 2),
            separator=" ",
        )
        for part in parts:
            raw_chunks.append(
                {
                    "speaker": turn["speaker"],
                    "speakers": [turn["speaker"]],
                    "section": turn["section"],
                    "turn_start": turn_index,
                    "turn_end": turn_index,
                    "body_chars": len(part),
                    "text": f"{prefix}{part}",
                }
            )
    merged = _merge_short_turns(raw_chunks, max_chars, effective_min)
    chunks: list[dict[str, Any]] = []
    for sequence, item in enumerate(merged, start=1):
        chunks.append(
            {
                "speaker": item["speaker"],
                "speakers": item["speakers"],
                "section": item["section"],
                "sequence": sequence,
                "turn_start": item["turn_start"],
                "turn_end": item["turn_end"],
                "paragraph_id": (
                    f"turn-{item['turn_start']:03d}-to-{item['turn_end']:03d}-block-{sequence:03d}"
                ),
                "text": item["text"],
            }
        )
    return chunks


def microsoft_ir_html(raw: bytes) -> list[dict[str, Any]]:
    text = html_to_text(raw.decode("utf-8", errors="replace"))
    return chunk_turns(split_speaker_turns(text))


def plain_text(raw: bytes) -> list[dict[str, Any]]:
    return chunk_turns(split_plain_text_turns(raw.decode("utf-8-sig", errors="replace")))


ADAPTERS: dict[str, Callable[[bytes], list[dict[str, Any]]]] = {
    "microsoft_ir_html": microsoft_ir_html,
    "plain_text": plain_text,
}


def transcript_turns(raw: bytes, adapter: str) -> list[dict[str, str]]:
    if adapter == "microsoft_ir_html":
        text = html_to_text(raw.decode("utf-8", errors="replace"))
        return split_speaker_turns(text)
    if adapter == "plain_text":
        return split_plain_text_turns(raw.decode("utf-8-sig", errors="replace"))
    raise ValueError(f"Unsupported transcript turn adapter: {adapter}")


def seed_neo4j(
    driver: Any,
    source: TranscriptSource,
    raw: bytes,
    chunks: list[dict[str, str]],
    vectors: list[list[float]],
    database: str,
    turns: list[dict[str, str]] | None = None,
) -> None:
    captured_at = datetime.now(UTC).isoformat()
    rows = [
        {
            **chunk,
            "chunk_id": f"{source.source_id}-{chunk['paragraph_id']}",
            "co_code": source.co_code,
            "source_id": source.source_id,
            "source_type": "transcript",
            "title": source.title,
            "period": source.period,
            "fiscal_label": source.fiscal_label,
            "event_date": source.event_date,
            "embedding": vector,
            "captured_at": captured_at,
            "content_hash": sha256(chunk["text"]),
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    source_turns = turns or [
        {
            "speaker": chunk["speaker"],
            "section": chunk["section"],
            "text": chunk["text"],
        }
        for chunk in chunks
    ]
    turn_rows: list[dict[str, Any]] = []
    sequence = 0
    for source_turn_sequence, turn in enumerate(source_turns, start=1):
        parts = build_semantic_blocks(
            [turn["text"]],
            max_chars=4_000,
            separator=" ",
        )
        for part_index, content in enumerate(parts, start=1):
            sequence += 1
            turn_rows.append(
                {
                    **turn,
                    "text": content,
                    "turn_id": (
                        f"{source.source_id}-turn-{source_turn_sequence:03d}-part-{part_index:02d}"
                    ),
                    "sequence": sequence,
                    "source_turn_sequence": source_turn_sequence,
                    "part_index": part_index,
                    "speaker_title": turn.get("title")
                    or source.speaker_titles.get(turn["speaker"]),
                    "co_code": source.co_code,
                    "source_id": source.source_id,
                    "period": source.period,
                    "fiscal_label": source.fiscal_label,
                    "event_date": source.event_date,
                    "captured_at": captured_at,
                    "content_hash": sha256(content),
                }
            )
    driver.execute_query(
        """
        MERGE (company:Company {co_code: $co_code})
          SET company.name = $company_name
        MERGE (document:Document {source_id: $source_id})
          SET document:EarningsCall
          SET document.co_code = $co_code,
              document.source_type = 'transcript',
              document.title = $title,
              document.period = $period,
              document.fiscal_label = $fiscal_label,
              document.event_date = $event_date,
              document.live_url = $live_url,
              document.captured_at = $captured_at,
              document.content_hash = $content_hash,
              document.data_version = $data_version,
              document.material_kind = $material_kind,
              document.official_source = true
        MERGE (company)-[:HAS_DOCUMENT]->(document)
        MERGE (company)-[:HAS_EARNINGS_CALL]->(document)
        """,
        co_code=source.co_code,
        company_name=source.company_name,
        source_id=source.source_id,
        title=source.title,
        period=source.period,
        fiscal_label=source.fiscal_label,
        event_date=source.event_date,
        live_url=source.url,
        captured_at=captured_at,
        content_hash="sha256:" + hashlib.sha256(raw).hexdigest(),
        data_version=source.data_version,
        material_kind=source.material_kind,
        database_=database,
    )
    driver.execute_query(
        """
        MATCH (call:EarningsCall {source_id: $source_id})-[:HAS_TURN]->(stale:SpeakerTurn)
        WHERE NOT stale.turn_id IN $turn_ids
        DETACH DELETE stale
        """,
        source_id=source.source_id,
        turn_ids=[row["turn_id"] for row in turn_rows],
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $turns AS item
        MATCH (call:EarningsCall {source_id: item.source_id})
        MERGE (turn:SpeakerTurn {turn_id: item.turn_id})
          SET turn.co_code = item.co_code,
              turn.source_id = item.source_id,
              turn.period = item.period,
              turn.fiscal_label = item.fiscal_label,
              turn.event_date = item.event_date,
              turn.sequence = item.sequence,
              turn.source_turn_sequence = item.source_turn_sequence,
              turn.part_index = item.part_index,
              turn.section = item.section,
              turn.speaker = item.speaker,
              turn.speaker_title = item.speaker_title,
              turn.text = item.text,
              turn.captured_at = item.captured_at,
              turn.content_hash = item.content_hash,
              turn.data_version = $data_version
        MERGE (call)-[:HAS_TURN]->(turn)
        """,
        turns=turn_rows,
        data_version=source.data_version,
        database_=database,
    )
    driver.execute_query(
        """
        MATCH (document:Document {source_id: $source_id})-[:HAS_CHUNK]->(stale:Chunk)
        WHERE NOT stale.chunk_id IN $chunk_ids
        DETACH DELETE stale
        """,
        source_id=source.source_id,
        chunk_ids=[row["chunk_id"] for row in rows],
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $rows AS item
        MATCH (document:Document {source_id: item.source_id})
        MERGE (chunk:Chunk {chunk_id: item.chunk_id})
          SET chunk.co_code = item.co_code,
              chunk.source_id = item.source_id,
              chunk.source_type = item.source_type,
              chunk.title = item.title,
              chunk.period = item.period,
              chunk.fiscal_label = item.fiscal_label,
              chunk.event_date = item.event_date,
              chunk.speaker = item.speaker,
              chunk.speakers = item.speakers,
              chunk.section = item.section,
              chunk.text = item.text,
              chunk.sequence = item.sequence,
              chunk.paragraph_id = item.paragraph_id,
              chunk.embedding = item.embedding,
              chunk.captured_at = item.captured_at,
              chunk.content_hash = item.content_hash,
              chunk.data_version = $data_version
        MERGE (document)-[:HAS_CHUNK]->(chunk)
        """,
        rows=rows,
        data_version=source.data_version,
        database_=database,
    )
    driver.execute_query(
        """
        UNWIND $rows AS item
        MATCH (chunk:Chunk {chunk_id: item.chunk_id})
        MATCH (turn:SpeakerTurn {source_id: item.source_id})
        WHERE turn.source_turn_sequence >= item.turn_start
          AND turn.source_turn_sequence <= item.turn_end
        MERGE (chunk)-[:CONTAINS_TURN]->(turn)
        """,
        rows=rows,
        database_=database,
    )


def remove_legacy_speaker_graph(driver: Any, database: str) -> None:
    """Remove the former global-person model after speaker snapshots moved to turns."""
    driver.execute_query(
        "MATCH ()-[relation:HAS_PARTICIPANT]->() DELETE relation",
        database_=database,
    )
    driver.execute_query(
        "MATCH ()-[relation:SPOKEN_BY]->() DELETE relation",
        database_=database,
    )
    driver.execute_query(
        "MATCH (speaker:Speaker) DETACH DELETE speaker",
        database_=database,
    )


def ingest(source_keys: list[str]) -> dict[str, Any]:
    settings = get_settings()
    sources = [SOURCES[key] for key in source_keys]
    raw_root = PROJECT_ROOT / "data" / "raw" / "earnings_calls"
    normalized: list[
        tuple[TranscriptSource, bytes, list[dict[str, str]], list[dict[str, Any]]]
    ] = []
    with httpx.Client(
        headers={"User-Agent": settings.sec_user_agent},
        timeout=60,
        follow_redirects=True,
    ) as client:
        for source in sources:
            response = client.get(source.url)
            response.raise_for_status()
            raw = response.content
            suffix = ".txt" if source.adapter == "plain_text" else ".html"
            path = raw_root / source.co_code.lower() / f"{source.source_key}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            turns = transcript_turns(raw, source.adapter)
            chunks = chunk_turns(turns)
            normalized.append((source, raw, turns, chunks))

    all_texts = [chunk["text"] for _, _, _, chunks in normalized for chunk in chunks]
    vectors = embed(all_texts, settings.ollama_url, settings.ollama_embedding_model)
    if not vectors or len(vectors) != len(all_texts):
        raise RuntimeError("Embedding count does not match transcript chunk count")

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        create_indexes(
            driver,
            len(vectors[0]),
            settings.neo4j_database,
            settings.neo4j_vector_index,
            settings.neo4j_fulltext_index,
        )
        remove_legacy_speaker_graph(driver, settings.neo4j_database)
        offset = 0
        details = []
        for source, raw, turns, chunks in normalized:
            source_vectors = vectors[offset : offset + len(chunks)]
            offset += len(chunks)
            seed_neo4j(
                driver,
                source,
                raw,
                chunks,
                source_vectors,
                settings.neo4j_database,
                turns,
            )
            details.append(
                {
                    "source_id": source.source_id,
                    "co_code": source.co_code,
                    "period": source.period,
                    "event_date": source.event_date,
                    "material_kind": source.material_kind,
                    "chunks": len(chunks),
                    "turns": len(turns),
                    "url": source.url,
                }
            )
    finally:
        driver.close()
    return {
        "sources": len(sources),
        "chunks": len(all_texts),
        "embedding_dimensions": len(vectors[0]),
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official earnings call transcripts")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=sorted(SOURCES),
        default=sorted(SOURCES),
    )
    args = parser.parse_args()
    import json

    print(json.dumps(ingest(args.sources), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
