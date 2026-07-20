# Global Engineering Instructions

Follow repository-specific instructions when they exist. Treat this file as the
default workflow for projects without more specific guidance.

## Core Objective

Produce correct, minimal, maintainable, and verifiable changes.

Do not optimize for producing large amounts of code. Optimize for solving the
requested problem with the smallest coherent change.

## Security and Confidentiality

- Treat all source code, configuration, logs, documents, and data as confidential.
- Do not copy project content to external services, websites, providers, or MCP
  servers unless they are explicitly approved.
- Do not add or enable external AI providers, telemetry, plugins, or network
  integrations without explicit authorization.
- Never expose credentials, tokens, internal URLs, personal data, or secrets.
- Never place secrets in source code, tests, logs, examples, or error messages.
- Follow the organization's existing security and access-control policies.

## Before Making Changes

1. Read the nearest project `AGENTS.md`, relevant documentation, and configuration.
2. Search for existing implementations, conventions, and tests.
3. Understand the current behavior before proposing a change.
4. Identify the root cause instead of modifying the first suspicious file.
5. Check whether the request affects public APIs, schemas, compatibility, security,
   or persistent data.
6. Do not invent files, APIs, commands, dependencies, or system behavior.

For non-trivial work, provide a concise plan containing:

- current understanding;
- files or components likely to change;
- implementation steps;
- verification approach;
- important risks or unknowns.

Do not stop after planning when implementation was requested, unless a material
decision, permission, or required input is missing.

## Task Scope

- Make the smallest coherent change that fully satisfies the request.
- Avoid unrelated refactoring, formatting, renaming, or cleanup.
- Preserve existing behavior unless the requested change requires otherwise.
- Prefer existing abstractions and project patterns over new frameworks.
- Do not add a dependency when the task can reasonably use existing code.
- Do not change lockfiles unless dependencies intentionally change.
- If requirements are ambiguous but the decision is local and reversible, make a
  reasonable assumption and state it.
- Ask before making decisions that affect external contracts, persistent data,
  security boundaries, or broad architecture.

## Implementation Quality

- Write code that matches the language and repository conventions.
- Prefer clear and direct code over clever or overly abstract code.
- Keep functions and modules focused on one responsibility.
- Handle errors explicitly and preserve useful diagnostic information.
- Validate inputs at appropriate boundaries.
- Preserve backward compatibility when it is part of the existing contract.
- Avoid duplicated logic when an established reusable implementation exists.
- Do not create speculative abstractions for hypothetical future needs.
- Comments should explain non-obvious reasons, constraints, or tradeoffs.
- Do not add comments that merely repeat the code.

## Debugging Workflow

When diagnosing a problem:

1. Reproduce or precisely characterize the failure.
2. Gather evidence from code, tests, logs, and configuration.
3. Trace the relevant execution and data flow.
4. Form a specific root-cause hypothesis.
5. Test the hypothesis with the smallest useful experiment.
6. Implement the fix only after the evidence supports it.
7. Add a regression test when practical.

Do not hide symptoms with broad exception handling, arbitrary retries, disabled
validation, or weakened tests.

## Verification

After each meaningful change, run the narrowest relevant verification first.

Before completion, run all applicable checks available in the repository:

- focused tests;
- regression tests;
- type checking;
- linting and formatting checks;
- build or compile;
- schema or migration validation;
- security or static analysis checks.

Also:

- Inspect the final diff for accidental or unrelated changes.
- Confirm new behavior against every acceptance criterion.
- Do not claim that a command passed unless it was actually executed.
- If a check cannot be executed, state exactly why.
- Never delete, skip, weaken, or rewrite a valid test only to make the result pass.
- Treat existing test failures separately from failures introduced by the change.

## Tool Usage

- Search before editing.
- Prefer precise code search and symbol navigation over reading entire repositories.
- Read relevant neighboring code and tests before creating new implementations.
- Use language servers, compiler diagnostics, and structured search when available.
- Avoid repeatedly reading the same large files.
- Avoid destructive commands unless explicitly required and the target is verified.
- Do not overwrite or discard unrelated user changes.

## Multi-Agent and Delegation

Use additional agents only when tasks are independent or benefit from specialized
analysis.

Good delegation examples:

- read-only codebase exploration;
- documentation research using approved sources;
- independent architecture or security review;
- testing separate hypotheses.

Delegation rules:

- Keep one primary writer for a connected code change.
- Do not let multiple agents modify the same files concurrently.
- Give each agent a clear scope and expected output.
- Require agents to return evidence, file paths, and verification results.
- Review delegated output before using it.
- Do not use multi-agent orchestration for trivial or single-file changes.
- More agents are not automatically better.

## Completion Report

When work is complete, report:

1. What changed.
2. Why the change solves the problem.
3. Important files changed.
4. Verification commands actually executed and their results.
5. Assumptions, limitations, or remaining risks.

Keep the report concise and factual.
