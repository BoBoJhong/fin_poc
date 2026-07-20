from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

import httpx
from neo4j import GraphDatabase

from app.config import PROJECT_ROOT, get_settings
from scripts.ingest_sec import html_to_text
from scripts.init_data import create_indexes, embed, sha256


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
    ),
}

SPEAKER_PATTERN = re.compile(r"^([A-Z][A-Z .'-]+(?:, [A-Za-z .&'/-]+)?):\s*(.*)$")


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


def chunk_turns(turns: list[dict[str, str]], max_chars: int = 1_400) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for turn_index, turn in enumerate(turns, start=1):
        sentences = re.split(r"(?<=[.!?])\s+", turn["text"])
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            pieces = [
                sentence[offset : offset + max_chars]
                for offset in range(0, len(sentence), max_chars)
            ] or [sentence]
            for piece in pieces:
                if current and len(current) + len(piece) + 1 > max_chars:
                    parts.append(current)
                    current = ""
                current = f"{current} {piece}".strip()
        if current:
            parts.append(current)
        for part_index, part in enumerate(parts, start=1):
            chunks.append(
                {
                    "speaker": turn["speaker"],
                    "section": turn["section"],
                    "paragraph_id": f"turn-{turn_index:03d}-part-{part_index:02d}",
                    "text": (f"Speaker: {turn['speaker']}\nSection: {turn['section']}\n{part}"),
                }
            )
    return chunks


def microsoft_ir_html(raw: bytes) -> list[dict[str, str]]:
    text = html_to_text(raw.decode("utf-8", errors="replace"))
    return chunk_turns(split_speaker_turns(text))


ADAPTERS: dict[str, Callable[[bytes], list[dict[str, str]]]] = {
    "microsoft_ir_html": microsoft_ir_html,
}


def seed_neo4j(
    driver: Any,
    source: TranscriptSource,
    raw: bytes,
    chunks: list[dict[str, str]],
    vectors: list[list[float]],
    database: str,
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
            "event_date": source.event_date,
            "embedding": vector,
            "captured_at": captured_at,
            "content_hash": sha256(chunk["text"]),
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    driver.execute_query(
        """
        MERGE (company:Company {co_code: $co_code})
          SET company.name = $company_name
        MERGE (document:Document {source_id: $source_id})
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
        UNWIND $rows AS item
        MATCH (document:Document {source_id: item.source_id})
        MERGE (chunk:Chunk {chunk_id: item.chunk_id})
          SET chunk.co_code = item.co_code,
              chunk.source_id = item.source_id,
              chunk.source_type = item.source_type,
              chunk.title = item.title,
              chunk.period = item.period,
              chunk.event_date = item.event_date,
              chunk.speaker = item.speaker,
              chunk.section = item.section,
              chunk.text = item.text,
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


def ingest(source_keys: list[str]) -> dict[str, Any]:
    settings = get_settings()
    sources = [SOURCES[key] for key in source_keys]
    raw_root = PROJECT_ROOT / "data" / "raw" / "earnings_calls"
    normalized: list[tuple[TranscriptSource, bytes, list[dict[str, str]]]] = []
    with httpx.Client(
        headers={"User-Agent": settings.sec_user_agent},
        timeout=60,
        follow_redirects=True,
    ) as client:
        for source in sources:
            response = client.get(source.url)
            response.raise_for_status()
            raw = response.content
            path = raw_root / source.co_code.lower() / f"{source.source_key}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            chunks = ADAPTERS[source.adapter](raw)
            normalized.append((source, raw, chunks))

    all_texts = [chunk["text"] for _, _, chunks in normalized for chunk in chunks]
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
        offset = 0
        details = []
        for source, raw, chunks in normalized:
            source_vectors = vectors[offset : offset + len(chunks)]
            offset += len(chunks)
            seed_neo4j(
                driver,
                source,
                raw,
                chunks,
                source_vectors,
                settings.neo4j_database,
            )
            details.append(
                {
                    "source_id": source.source_id,
                    "co_code": source.co_code,
                    "period": source.period,
                    "event_date": source.event_date,
                    "material_kind": source.material_kind,
                    "chunks": len(chunks),
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
