# Feature Specification: Schema-Derived Frontend Types (End the Hand-Mirrored Type Layer)

**Feature Branch**: `020-openapi-type-generation`

**Created**: 2026-07-27

**Status**: Draft

**Input**: User description: "Eliminate the hand-mirrored type layer between the FastAPI backend and the React frontend. Today `frontend/src/api/client.ts` hand-declares the backend's data model (SessionTarget, InstanceStatus, RemoteCapability, TypedError, terminal control frames) with a comment that it mirrors the spec 'exactly', and `components/providerMeta.ts` re-encodes the provider-type and InstanceStatus enums including which statuses mean 'update required' — all with no automated check, so backend changes can silently drift from the frontend. Generate TypeScript types from the FastAPI OpenAPI schema as part of the frontend build: (1) a script that exports the app's OpenAPI JSON without running a server; (2) generated types checked in (or generated at build) and imported by api/client.ts in place of the hand-written interfaces; (3) a CI check that fails when the generated types are stale relative to the backend; (4) enums like InstanceStatus and provider type sourced from the schema so providerMeta.ts maps over schema-derived values instead of re-declaring them. The WebSocket control-frame shapes (remo-terminal.v1 JSON frames) are not in the REST schema — either publish them via a shared schema module included in the OpenAPI components, or explicitly document them as a separately versioned contract with its own drift test. Keep the frontend's ApiError normalization behavior unchanged; this is a type-provenance change, not a client rewrite."

## Overview

The browser console's API client currently restates the service's data model by hand. `frontend/src/api/client.ts` opens with a comment claiming its types "mirror … exactly" two spec documents, and `frontend/src/components/providerMeta.ts` independently re-encodes the instance-status vocabulary — including the product decision about which statuses mean "update required" — and the set of provider types. Nothing verifies either claim. A contributor can rename a service field, add a status value, or register a new provider and ship a green build while the console silently mis-renders or drops data.

This feature makes the service the single, machine-checked source of truth for every shape the console consumes, and makes drift a build failure with an actionable message — following the pattern already established in-repo by `tests/unit/test_docs_structure.py` (feature 019): generate the truth, diff it against the checked-in copy, fail naming exactly what drifted and how to regenerate it.

Delivering this requires the service's published contract to actually *describe* its surface. Several endpoints the console calls today return undeclared shapes, and status/provider fields are typed as free-form strings, so today's published schema could not supply the types even if the console imported it. Tightening that description is in scope; changing runtime behavior is not.

## Clarifications

### Session 2026-07-27

- Q: How should the console behave when it receives a vocabulary value (status, session state, container-running state) outside the closed schema-derived union? → A: Exhaustive at compile time; runtime fallback retained.
- Q: Where does the published "known provider types" vocabulary come from — the live provider registry at export time, or the built-in descriptors only? → A: Built-in descriptors only; third-party types intentionally absent.
- Q: Does the exported contract artifact cover the whole application or only the console-facing subset? → A: Whole app exported; console imports only the subset it uses.
- Q: Where does the single published definition of the `remo-terminal.v1` control frames live, given the service builds them as untyped dictionaries today? → A: Service-side typed frame models, used by the service's own send/parse path.
- Q: Is the exported contract artifact an externally supported API contract or an internal build input? → A: Internal build input; no external compatibility promise.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A service-side model change cannot silently diverge from the console (Priority: P1)

A contributor changes the service's response model — renames a field, adds a field, makes an optional field required, or removes one. They run the repository's checks. The build fails, naming the changed shape and telling them how to bring the console's copy back into agreement. After running the single documented regeneration command and committing the result, the console's type checker either passes (the change was additive and compatible) or points at the exact call sites that need updating.

**Why this priority**: This is the whole point of the feature. Without it, the remaining stories are cosmetic refactors. It is also the only story that changes the failure mode of a real, recurring mistake.

**Independent Test**: Introduce a field rename in a service response model on a scratch branch, run the repository check suite, and confirm it fails with a message naming the field and the regeneration command. Revert, regenerate, confirm green.

**Acceptance Scenarios**:

1. **Given** the console's generated types are in agreement with the service, **When** the repository check suite runs, **Then** the drift check passes and reports no findings.
2. **Given** a contributor renames a field in a service response shape but does not regenerate the console's types, **When** the repository check suite runs, **Then** it fails with a message that names the affected shape/field and states the exact command to regenerate.
3. **Given** a contributor adds a new required field to a service response shape and regenerates, **When** the console's type check runs, **Then** it passes without further edits (additive fields need no console change) and the new field is available to console code.
4. **Given** a contributor removes a field the console reads, **When** they regenerate and run the console's type check, **Then** it fails at the exact call sites that read the removed field.
5. **Given** a contributor with no service-side development environment, **When** they run only the console's checks, **Then** those checks still validate the console against the checked-in contract artifact and produce a clear message if that artifact is absent or unreadable.

---

### User Story 2 - The console's API types come from the service, not from a comment (Priority: P1)

A contributor reading `frontend/src/api/client.ts` sees request/response types that are provably derived from the service's published contract, not re-typed by hand. Nothing about how the console handles errors changes: the same normalized error object is thrown for HTTP-level failures, network-level failures, forward-auth challenges, and the pairing-mint forbidden case, with the same codes, retryability, and remediation text.

**Why this priority**: This is the deliverable contributors actually touch, and it is what makes Story 1's check meaningful — a drift check over types nobody imports proves nothing. It ships together with Story 1.

**Independent Test**: Confirm no hand-declared service-owned shape remains in the console's API layer, and that the existing console test suite passes unmodified — no test may need editing to accommodate the change.

**Acceptance Scenarios**:

1. **Given** the console's API layer, **When** it is inspected for declarations of service-owned response and request shapes, **Then** every such shape resolves to the generated artifact rather than a locally written declaration.
2. **Given** the existing console unit/component test suite, **When** it runs against the changed API layer without modification, **Then** every test passes.
3. **Given** a failing request (HTTP error envelope, transport failure, forward-auth challenge, or pairing forbidden), **When** the console handles it, **Then** the thrown error carries the same code, message, retryable flag, and remediation text as before this change.
4. **Given** console-owned concepts that are not part of the service contract (connection state, renderer options, layout persistence), **When** the API layer is changed, **Then** those remain locally declared and are not forced through the generated artifact.

---

### User Story 3 - Status and provider vocabularies are read from the schema, not re-typed (Priority: P2)

A contributor adds a new instance-status value to the service. The console's presentation layer, which maps statuses to labels and colors, fails to compile until the new value is given a presentation — rather than silently falling through to a generic default. Provider types behave the same way for the set the service knows about, while still rendering something sensible for a provider the service does not know about (third-party providers can register themselves, so this vocabulary is deliberately open).

Compile-time exhaustiveness and the runtime fallback solve different problems and both stay: exhaustiveness is what stops a contributor from forgetting a new value, while the fallback is what stops a browser holding a cached bundle from rendering nothing when a newer service sends a value that bundle has never heard of.

**Why this priority**: Delivers the specific silent failure the description calls out — `providerMeta.ts` re-encoding the status enum and the "update required" product decision — but the drift check and typed client (Stories 1–2) are the foundation it stands on.

**Independent Test**: Add a status value on a scratch branch, regenerate, and confirm the console's type check fails pointing at the presentation mapping; supply a presentation for it and confirm it passes.

**Acceptance Scenarios**:

1. **Given** the service's instance-status vocabulary, **When** the console's status presentation mapping is checked, **Then** it is exhaustive over the schema-derived values and a missing entry is a type error.
2. **Given** a newly added service-side status value, **When** the console is type-checked after regeneration, **Then** the type check fails at the presentation mapping — the contributor is stopped at build time rather than discovering the gap from a generic label in a running console.
3. **Given** the schema's known provider types, **When** the console renders an instance whose provider type is in that set, **Then** it uses that provider's label and accent.
4. **Given** an instance whose provider type is not in the schema's known set (a third-party provider), **When** the console renders it, **Then** it falls back to a neutral label and accent without erroring — the vocabulary is open, not closed.
5. **Given** the session-target state vocabularies (session state and container-running state), **When** the console consumes them, **Then** they are schema-derived on the same terms as instance status.
6. **Given** a running console that receives a status value outside its compiled union (for example a cached bundle talking to a newer service), **When** it renders that instance, **Then** it uses a neutral fallback presentation without throwing — the compile-time exhaustiveness requirement does not remove the runtime fallback.
7. **Given** the service's built-in providers, **When** the known-provider vocabulary is published, **Then** it contains exactly those built-in provider types and does not vary with which third-party providers happen to be installed.

---

### User Story 4 - The terminal control-frame contract has explicit, checked provenance (Priority: P3)

The versioned JSON control frames exchanged over the terminal socket (`remo-terminal.v1`) are not part of the REST surface, yet the console declares their shape by hand today. A contributor changing a frame's shape, adding a frame type, or adding an error class is stopped by a check, exactly as they would be for a REST shape.

**Why this priority**: A real drift surface, but a narrower and slower-moving one than the REST model, and it can land after the REST pipeline exists.

**Independent Test**: Change a control-frame shape on a scratch branch and confirm a check fails naming the frame and the remediation; confirm the terminal connection's behavior is unchanged when the shapes are merely re-sourced.

**Acceptance Scenarios**:

1. **Given** the control-frame shapes, **When** the console declares the frames it sends and receives, **Then** those declarations derive from a single published definition rather than a hand-written copy.
2. **Given** the service sends or parses a control frame, **When** it does so, **Then** it goes through the same typed definitions the contract artifact is exported from — the service cannot drift from its own published frame contract.
3. **Given** a change to a control-frame shape, frame type, or error class on the service side, **When** the repository check suite runs without a corresponding console update, **Then** it fails naming the frame and the regeneration/update step.
4. **Given** the control-frame contract carries its own version marker, **When** that version changes, **Then** the change is visible in the checked artifact and in the contract document.
5. **Given** the terminal connection's runtime behavior (reconnect budget and backoff, ping/pong latency reporting, close-code handling), **When** the frame shapes are re-sourced, **Then** that behavior is unchanged and the existing terminal tests pass unmodified.

---

### Edge Cases

- **Undescribed endpoints**: Several endpoints the console calls today publish no declared response shape (readiness, health, pairing-code minting), and the shared error envelope is not declared on any endpoint. Generation over the current contract would therefore produce nothing usable for those calls. The contract must describe them before the console can consume them.
- **Free-form vocabulary fields**: Status and provider-type fields are currently published as unconstrained strings even though the service holds a closed status enumeration internally. Generation would yield a bare string type and Story 3 would be unachievable without tightening the published description.
- **Non-deterministic export**: If two runs of the export produce byte-different output (ordering, formatting, embedded timestamps), the drift check flaps and gets disabled. Export must be reproducible.
- **Generator version drift**: If a dependency bump changes the generated output with no service change, the check fails without a real defect. The regeneration command and the pinned generator are part of the checked artifact's identity.
- **Missing optional install**: The service's web components are an optional install. Contributors and CI must get a clear message if the export is attempted without them, not an opaque import failure.
- **Console-only contributor**: Someone editing only the console has no service-side environment. Their local checks must still be meaningful and must not require the service toolchain.
- **Backward-compatible optional fields**: Some response fields are populated only by newer remote hosts and defaulted otherwise. Generated types must preserve the correct required/optional and defaulted semantics so existing conditional rendering keeps working.
- **Open provider vocabulary**: Third-party providers may register provider types the service's published set does not contain; a closed union here would break that extensibility and let real runtime data violate its own type.
- **Stale bundle, newer service**: A browser holding a cached bundle can receive a vocabulary value added after that bundle was built. Compile-time exhaustiveness must not be achieved by deleting the runtime fallback, or this case renders nothing.
- **Export depending on installed providers**: If the published provider vocabulary were read from the live registry, installing or removing a third-party provider would change the exported artifact and fail the drift check with no source change. The vocabulary must be fixed by the built-in set.
- **Service drifting from its own frame contract**: The service builds and parses control frames as untyped dictionaries today. A published frame definition that the service itself does not use would let the service change a frame while both the definition and the console stay green.
- **CLI-only surface**: Parts of the contract exist for the workstation CLI rather than the console. They appear in the exported artifact because they belong to the same application, but they must not be presented as console API surface.
- **Check message quality**: A failure that says only "types are stale" reproduces the problem the check was meant to solve. The message must name what drifted and the exact command to fix it.

## Requirements *(mandatory)*

### Functional Requirements

#### Published contract completeness

- **FR-001**: The service MUST publish a machine-readable description covering its whole REST surface — not a console-facing subset — including endpoints that today return undeclared shapes (readiness, health, pairing-code minting and ending). The console imports only the portion it uses; no filtering step stands between the application and the exported artifact.
- **FR-002**: The published description MUST include the structured error envelope on every route that actually returns it, so the console's error type is schema-derived rather than hand-written. The envelope is **not** universal across the service: some routes return a different failure shape, and the framework's own request-validation failures use a third. Each failure response MUST be published as the shape that route really returns — declaring a single envelope service-wide would publish a contract the service does not honor, which is the failure this feature exists to prevent.
- **FR-003**: Fields whose values come from a closed, service-owned vocabulary (instance status, session state, container-running state) MUST be published as that enumeration rather than as unconstrained strings.
- **FR-004**: The set of provider types the service knows about MUST be published as a named, discoverable vocabulary, while the wire field itself remains open so an unrecognized provider type is still valid data.
- **FR-004a**: The published provider vocabulary MUST be derived from the built-in provider set only and MUST NOT vary with which third-party providers are installed or registered at export time, so that the exported artifact stays reproducible (FR-007). Third-party provider types are intentionally absent from the vocabulary and are served by the open-fallback path (FR-014).
- **FR-005**: Publishing this description MUST NOT change any endpoint's runtime request handling, response payload, status codes, or error text.

#### Export

- **FR-006**: A single documented command MUST produce the published contract as a checked-in artifact without starting a server, binding a port, or requiring any remote host, credential, or registry state.
- **FR-007**: The export MUST be deterministic: repeated runs on unchanged sources MUST produce byte-identical output.
- **FR-008**: The export MUST fail with an actionable message naming the missing optional install when the service's web components are unavailable.

#### Generation and consumption

- **FR-009**: The console's types for service-owned request and response shapes MUST be generated from the exported contract artifact by a single documented command.
- **FR-010**: The console's API layer MUST consume those generated types in place of hand-written equivalents; no service-owned response or request shape may remain hand-declared there.
- **FR-011**: The console's error normalization behavior MUST be unchanged — same thrown error type, same code, message, retryable flag, and remediation values for HTTP error envelopes, transport failures, forward-auth challenges, and the pairing-mint forbidden case.
- **FR-012**: Console-owned concepts that are not part of the service contract MUST remain locally declared and MUST NOT be pushed through the generated artifact.
- **FR-013**: The console's status presentation mapping MUST be exhaustive over the schema-derived status vocabulary, such that a value present in the vocabulary but absent from the mapping is a compile-time failure.
- **FR-013a**: The console MUST retain a runtime fallback presentation for vocabulary values received off the wire that are outside its compiled union, and MUST NOT throw or render blank on them. FR-013 constrains what the compiler accepts; FR-013a constrains what the running console does with a value it was never compiled against.
- **FR-014**: The console's provider presentation MUST enumerate the schema-derived known provider types and MUST still render an unrecognized provider type with a neutral fallback.

#### Drift detection

- **FR-015**: An automated check MUST fail when the checked-in contract artifact does not match what the service currently publishes.
- **FR-016**: An automated check MUST fail when the checked-in generated console types do not match what the contract artifact currently yields.
- **FR-017**: Every drift check defined by this feature (contract freshness, generated-type freshness, and frame-contract freshness) MUST run in continuous integration on every pull request and MUST NOT be skippable by configuration or by omitting an optional install — an environment that cannot run a check MUST fail it, not silently pass it.
- **FR-018**: Failure messages MUST name what drifted (the affected paths, shapes, or fields) and MUST state the exact command to regenerate, following the message conventions already established by the repository's documentation-structure drift gate.
- **FR-019**: The checks MUST NOT modify tracked files as a side effect of running; regeneration MUST be an explicit, separate action taken by the contributor.
- **FR-020**: The console's own checks MUST fail with a clear message if the checked-in contract artifact is missing or unparseable, rather than silently generating nothing.

#### Terminal control frames

- **FR-021**: The `remo-terminal.v1` JSON control-frame shapes — the browser-to-service frames, the service-to-browser frames, the version marker, and the error-class vocabulary — MUST have a single published definition, authored on the service side, that both the exported frame artifact and the console's declarations derive from.
- **FR-021a**: The service MUST construct and parse control frames through that same definition rather than through ad-hoc dictionary literals, so the service cannot silently diverge from the contract it publishes. This replaces the current untyped frame construction and parsing.
- **FR-022**: A change to any control-frame shape, frame type, or error class without a corresponding console update MUST fail an automated check with a message naming the frame and the remediation.
- **FR-023**: The control-frame contract MUST carry an explicit version identifier, and its versioning MUST be documented as independent of the REST contract's evolution.
- **FR-024**: Re-sourcing the frame shapes MUST NOT change the terminal connection's runtime behavior (reconnect budget and backoff, ping/pong latency reporting, close-code handling, binary/text framing).
- **FR-025**: The control-frame definition MUST be published as a separately versioned contract artifact with its own dedicated drift check, kept out of the REST contract document. Its failure message and remediation style MUST match the REST drift check so the two read as one family.

#### Documentation

- **FR-026**: Contributor documentation MUST state how to regenerate the artifacts, when regeneration is required, and how to interpret each drift-check failure — reachable from the same place the existing documentation-structure drift gate is documented.
- **FR-027**: The stale claim in the console's API layer that its types "mirror the spec exactly" MUST be removed or replaced with an accurate statement of provenance.
- **FR-028**: Repository orientation documents MUST be updated to describe the new artifacts and commands, satisfying the existing structure-drift gate.
- **FR-029**: The exported contract artifact MUST be documented as an internal build input, not an externally supported API contract. It carries no compatibility promise to third-party consumers, and its evolution imposes no deprecation policy — the service and the console ship together.

### Key Entities

- **Published contract artifact**: The checked-in, machine-readable description of the service's whole REST surface — the single source of truth from which console types are generated and against which drift is measured. Reproducible from the service's sources by one command. An internal build input, not an external compatibility promise.
- **Generated console types**: The derived type declarations the console's API layer imports. Never edited by hand; regenerated from the contract artifact.
- **Status vocabulary**: The closed set of instance-status values the service can report, together with the console-side presentation decision (label, accent, whether it signals an operator action such as "update required"). The values are service-owned; the presentation is console-owned but must be exhaustive over them.
- **Provider vocabulary**: The built-in provider types, published so the console does not re-declare them. Fixed by the built-in set rather than by what is installed, and deliberately open on the wire so third-party providers remain renderable.
- **Terminal control-frame contract**: The versioned JSON frames exchanged over the terminal socket, distinct from the REST surface and carrying its own version marker. Defined once on the service side and used by the service's own send/parse path, not merely described.
- **Drift check**: An automated comparison between generated truth and a checked-in copy that fails with a message naming what drifted and how to fix it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero service-owned request/response shapes remain hand-declared in the console's API layer (counted by inspection; target is exactly 0, down from at least 12 today).
- **SC-002**: A deliberately introduced service-side field rename fails the repository's checks 100% of the time, and the failure message names the affected shape/field and the regeneration command.
- **SC-003**: A deliberately introduced new status value, once regenerated, fails the console's type check at the status presentation mapping — the contributor is stopped at build time rather than shipping a value that renders with the generic fallback.
- **SC-004**: Regenerating every artifact takes exactly two documented commands — one per toolchain, because the service side and the console side have no shared runtime — and requires no running service, no network access, and no credentials, completing in under 60 seconds combined on a developer machine.
- **SC-005**: Running the export twice on unchanged sources produces byte-identical output 100% of the time (verified over at least 3 consecutive runs).
- **SC-006**: Every REST endpoint the console calls (currently 9) plus the shared error envelope appears in the published contract; none is absent.
- **SC-007**: The existing console test suite and the existing service test suite both pass with zero test files modified to accommodate this change, demonstrating unchanged behavior.
- **SC-008**: A contributor who has never seen this pipeline can go from a red drift check to green using only the failure message and the linked documentation, without reading the check's source.
- **SC-009**: An instance reporting a provider type the service does not know about still renders in the console without an error, confirming the provider vocabulary stayed open.
- **SC-010**: A vocabulary value outside the console's compiled union renders with a neutral fallback rather than throwing or rendering blank, verified by a test that feeds the console an off-union status value.
- **SC-011**: Installing or removing a third-party provider produces no change in the exported contract artifact (byte-identical before and after), confirming the published vocabulary is fixed by the built-in set.
- **SC-012**: Every control frame the service sends or parses goes through the published frame definition — zero ad-hoc frame dictionary literals remain in the service's terminal socket path (down from 5 today).

## Assumptions

- **Checked-in artifacts, not build-time-only generation.** Both the contract artifact and the generated types are committed to the repository. The description allowed either, but a drift check needs a committed baseline to be stale *relative to*, this matches the precedent set by the documentation-structure gate, and it keeps the console's build and its contributors free of a service-side toolchain dependency.
- **Message and failure-style precedent.** The drift checks follow `tests/unit/test_docs_structure.py`: generate the truth, diff against the checked-in copy, fail with a grouped message that names each drifted item and closes with the exact remediation command plus a link to a contributor how-to. The checks should read as one family, not three inventions.
- **Control frames are a separate contract (decided, not assumed).** The description offered two options — fold the frame shapes into the REST document's components, or publish them separately. The separate versioned artifact was chosen (FR-025): the REST document stays free of schemas no REST path references, and the frame contract can version on its own cadence. The cost accepted is a second artifact and a third drift check.
- **Ordering advice from the original backlog is moot.** The note that this work was "best landed before any API surface changes" no longer applies — the referenced surface changes have already merged, including fields added to the setup status and registry endpoints. The generated types simply capture the current surface, additions included.
- **Provider vocabulary stays open on the wire, and fixed at export.** The formal provider abstraction (feature 018) explicitly supports third-party providers registering new types without touching existing files. Closing the provider-type field to an enumeration would break that promise and would let real runtime data violate its own type. Two decisions follow: the field stays open (FR-004, FR-014), and the *published* vocabulary is derived from the built-in providers only (FR-004a) so that a third-party install cannot change the exported artifact and redden the drift check with no source change.
- **Compile-time exhaustiveness and the runtime fallback are both required.** They answer different questions — "did a contributor forget to handle a new value?" and "what does a running browser do with a value it was never compiled against?". FR-013 does not license deleting the fallback branch that exists today (FR-013a, SC-010).
- **The frame contract is executable, not just documented.** The service currently builds control frames as bare dictionary literals at five call sites and parses them by key lookup, so there is no service-side definition to publish yet. Creating one and routing the service's own send/parse path through it (FR-021a) is in scope; without it the "published definition" would be a document the service itself could contradict.
- **Tightening the published description is in scope; changing behavior is not.** Adding declared response shapes, declaring the error envelope, and narrowing vocabulary fields to their existing closed enumerations are all necessary for generation to yield anything useful. None of them changes what the service actually sends today.
- **Test fixtures follow the generated types.** Console test fixtures that construct service-shaped objects will be typed by the generated artifact; where that surfaces a fixture that was already wrong, the fixture is corrected rather than the type loosened. This does not count as "modifying tests to accommodate the change" under SC-007 unless a behavioral assertion changes.
- **CLI-facing setup types are generated but not consumed.** The whole application is exported (FR-001), so the workstation-CLI-facing endpoints appear in the artifact. Filtering them out would require a maintained allowlist that is itself a drift surface. They are not console API surface and the console will not import them.
- **The artifact is an internal build input.** It is not published as a supported contract for third-party clients (FR-029). The service and the console are built and shipped from the same repository and image, so there is no external consumer to owe a deprecation policy to. If that ever changes, versioning and compatibility become a separate feature.
- **The pinned generator is part of the artifact's identity.** A generator upgrade is expected to require a regeneration commit, the same way a formatter upgrade does; that is acceptable and will be documented rather than engineered around.
- **No new runtime dependency for the service.** Contract export uses capability the service's existing framework already provides. New tooling, if any, is a development/build-time dependency of the console.
