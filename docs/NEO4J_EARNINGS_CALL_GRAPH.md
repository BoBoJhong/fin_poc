# Neo4j Earnings-Call Graph Contract

## Purpose

This schema separates transcript reading from semantic retrieval:

- `SpeakerTurn` preserves ordered, single-speaker verbatim text for transcript APIs.
- `Chunk` is the bounded embedding unit used by RAG and may cover multiple short turns.
- `EarningsCall` identifies one official event and is also labeled `Document` for compatibility
  with the existing evidence and source-preview pipeline.

## Architecture assessment

This graph is a good fit for the current earnings-call RAG because it separates three concerns:

- `EarningsCall` owns event identity, company scope, fiscal label and provenance.
- `SpeakerTurn` preserves the complete ordered conversation independently of embedding size.
- `Chunk` is optimized for vector/full-text retrieval and can change without changing the source
  dialogue contract.

The main deliberate compatibility cost is that an earnings call has both `EarningsCall` and
`Document` labels and uses both `HAS_EARNINGS_CALL` and `HAS_DOCUMENT`. Keep those relationships
while the generic document pipeline still consumes them; do not add another transcript copy.

The graph does not by itself guarantee answer quality. Ingestion validation, exact company/period
filters, content hashes, fiscal-label mapping and evidence tests remain required boundaries.

## Canonical graph

```text
(:Company {co_code})
  -[:HAS_EARNINGS_CALL]->
(:EarningsCall:Document {source_id, period, fiscal_label, event_date})
  -[:HAS_TURN]->
(:SpeakerTurn {turn_id, sequence, speaker, speaker_title, text})

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
| SpeakerTurn | `turn_id = source_id + source turn + part` | One turn segment belongs to one call and stores that event's speaker snapshot |
| Chunk | `chunk_id` | One call has many retrieval chunks |

`SpeakerTurn.sequence` is the canonical display order. A very long source turn is split at a
semantic boundary into segments of at most 4,000 characters. The segments retain the same speaker
and `source_turn_sequence`; they are never merged with another speaker.

## Company identity and aliases

### Implemented state

Neo4j currently stores company identity on the canonical node:

```text
(:Company {co_code, name, industry?})
```

- `co_code` is the unique stable key.
- `name` is the canonical company name.
- `industry` is optional and is populated by sources that provide it.
- There is currently no `CompanyAlias` node and no canonical alias array on the Neo4j node.

Natural-language aliases belong to the configured MariaDB company master in production (or the
SQLite company master in local PoC mode):

```text
company_aliases(co_code, alias, alias_type)
```

The company resolver loads this table into `CompanySummary.aliases`, then matches the formal name,
aliases and stock code before any Neo4j transcript query. This makes the company master the single
source of truth and avoids alias drift between SQLite and Neo4j.

Current resolution path:

```text
natural-language company reference
  -> configured MariaDB company master (or local SQLite PoC master)
  -> canonical co_code
  -> MATCH (:Company {co_code}) in Neo4j
```

For the current service, keeping aliases in the company master is recommended. An alias does not
need to become a graph node merely to support natural-language lookup.

### Optional Neo4j-native alias model

Add alias nodes only if Neo4j must independently support alias provenance, language, validity dates,
conflicting aliases or graph-native company discovery:

```text
(:CompanyAlias {
  alias_id,
  value,
  normalized_value,
  alias_type,
  language,
  source,
  valid_from?,
  valid_to?
})-[:ALIAS_OF]->(:Company)
```

Use `alias_id = co_code + normalized alias + alias type` as the stable key. Do not make
`normalized_value` globally unique because one alias may legitimately be ambiguous across
companies. If this model is introduced, synchronize it from the company master rather than
maintaining two independently editable alias stores.

## Speaker/title policy

There is deliberately no global `Speaker` node. The product only needs the speaker identity and job
title stated for that call, so both are event-scoped properties on `SpeakerTurn`.

- `SpeakerTurn.speaker` stores the source-stated name.
- `SpeakerTurn.speaker_title` stores the source-stated title for that call.
- If the official source does not state a title, store `null`; do not infer it from model memory.
- `prepared_remarks` and `question_and_answer` belong in `SpeakerTurn.section`.
- Repeated names across calls are not identity-resolved. This avoids same-name collisions and stale
  job titles when participants change.

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

- Unique: `Company.co_code`, `Document.source_id`, `SpeakerTurn.turn_id`, `Chunk.chunk_id`.
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

## Migration from the former global Speaker model

The ingestion command removes legacy `HAS_PARTICIPANT`, `SPOKEN_BY`, and `Speaker` records before
publishing the simplified graph. Speaker name/title remain available because they were already
stored on every `SpeakerTurn`; public MCP response fields do not change.
