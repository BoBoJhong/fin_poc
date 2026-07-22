# Neo4j Earnings-Call Graph Contract

## Purpose

This schema separates transcript reading from semantic retrieval:

- `SpeakerTurn` preserves ordered, single-speaker verbatim text for transcript APIs.
- `Chunk` is the bounded embedding unit used by RAG and may cover multiple short turns.
- `EarningsCall` identifies one official event and is also labeled `Document` for compatibility
  with the existing evidence and source-preview pipeline.

## Canonical graph

```text
(:Company {co_code})
  -[:HAS_EARNINGS_CALL]->
(:EarningsCall:Document {source_id, period, fiscal_label, event_date})
  -[:HAS_TURN]->
(:SpeakerTurn {turn_id, sequence, speaker, speaker_title, text})
  -[:SPOKEN_BY]->
(:Speaker {speaker_id, co_code, name})

(:EarningsCall)-[:HAS_PARTICIPANT {title, source_id, period, data_version}]->(:Speaker)
(:EarningsCall)-[:HAS_CHUNK]->(:Chunk {chunk_id, embedding, ...})
(:Chunk)-[:CONTAINS_TURN]->(:SpeakerTurn)
```

The compatibility relationships remain:

```text
(:Company)-[:HAS_DOCUMENT]->(:Document)
(:Document)-[:HAS_CHUNK]->(:Chunk)
```

## Identity and cardinality

| Entity | Stable key | Cardinality |
|---|---|---|
| Company | `co_code` | One company has many earnings calls |
| EarningsCall | `source_id` | One call belongs to one company |
| Speaker | `speaker_id = co_code + normalized name` | One speaker may join many calls |
| SpeakerTurn | `turn_id = source_id + source turn + part` | One turn segment belongs to one call and one speaker |
| Chunk | `chunk_id` | One call has many retrieval chunks |

`SpeakerTurn.sequence` is the canonical display order. A very long source turn is split at a
semantic boundary into segments of at most 4,000 characters. The segments retain the same speaker
and `source_turn_sequence`; they are never merged with another speaker.

## Role/title policy

`title` means the participant's job title, never transcript section or document title.

- Store an official event-specific title on `HAS_PARTICIPANT.title` and copy it to
  `SpeakerTurn.speaker_title` for deterministic reads.
- Do not store a changing job title as a global truth on `Speaker`.
- If the official source does not state a title, store `null`; do not infer it from model memory.
- `prepared_remarks` and `question_and_answer` belong in `SpeakerTurn.section`.

## Source and isolation rules

- Public transcript reads match `official_source = true`, `source_type = transcript`, and the exact
  `co_code` before selecting a call.
- “Latest” means the greatest verified `event_date`/available period for that company; it never uses
  another company's call or the current date as a substitute.
- RAG first filters `co_code`, period, source type, and an explicitly named known speaker before
  vector scoring.
- `content` returned by the transcript reader is verbatim `SpeakerTurn.text`. Summaries belong to
  answer fields, not transcript content.

## Indexes and constraints

- Unique: `Company.co_code`, `Document.source_id`, `Speaker.speaker_id`, `SpeakerTurn.turn_id`,
  `Chunk.chunk_id`.
- Range index: `EarningsCall(co_code, event_date)`.
- Composite vector filter fields: `Chunk.co_code`, `Chunk.period`, `Chunk.source_type`.
- Full-text index: `Chunk.text`, `Chunk.title`.

## Read patterns

Latest official call:

```cypher
MATCH (call:EarningsCall {co_code: $co_code})
WHERE call.official_source = true AND call.source_type = 'transcript'
RETURN call
ORDER BY call.event_date DESC
LIMIT 1
```

Ordered, cursor-based conversation page:

```cypher
MATCH (:EarningsCall {source_id: $source_id})-[:HAS_TURN]->(turn:SpeakerTurn)
WHERE turn.sequence > $cursor
RETURN turn.sequence, turn.speaker, turn.speaker_title, turn.text
ORDER BY turn.sequence
LIMIT $limit
```

