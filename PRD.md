# PBBot — Product Requirements Document

**Version:** 1.0 draft  
**Status:** For team review  
**Product owner:** Point Blank Community Team  
**Last updated:** 22 July 2026

## 1. Product summary

PBBot is a WhatsApp-first community operations platform. It gives administrators a deterministic way to manage members, labels, events, event-specific forms, tasks, reminders, progress updates, reports, and audit history. Members use WhatsApp to see their work and submit updates.

The command interface is the primary interface. Natural language is an optional convenience layer that translates user intent into one or more predefined operations. Natural-language interpretation must never write to the database directly.

## 2. Problem statement

Community operations are currently difficult to track consistently across WhatsApp messages, spreadsheets, and ad-hoc reminders. This creates three problems:

1. Members do not have a reliable view of their assigned work and current status.
2. Admins spend time manually assigning work, following up, and compiling reports.
3. The team lacks an immutable history of who changed what and when.

PBBot should make routine community operations visible, repeatable, and auditable without requiring members to learn a new application.

## 3. Goals

- Provide a WhatsApp command interface for members and admins.
- Make MongoDB the source of truth for all operational data.
- Represent every state-changing action as a validated operation.
- Support both participation programs and organization-managed events.
- Track assignments, updates, reminders, missed follow-ups, and completion.
- Provide role-based access control and immutable audit records.
- Keep natural language optional and safely bounded by the operation layer.

## 4. Non-goals for v1

- Replacing WhatsApp as the communication channel.
- Building a general-purpose CRM or project-management product.
- Allowing an LLM to query or mutate MongoDB directly.
- Automatic interpretation of ambiguous requests without confirmation.
- Native mobile or desktop applications.
- Full analytics, billing, SSO, or multi-tenant organization management.

## 5. Initial scope: migrate the existing bot

The first PBBot capability is a compatibility-preserving migration of the existing Shreyash bot into the OpenWA + PBBot architecture. This gives the team a known working slice while establishing the boundaries that all later features will use.

The migration shall preserve the existing user-visible behavior:

- `!stats` returns the current PBCTF statistics snapshot in configured WhatsApp groups.
- A scheduled statistics broadcast runs nightly at midnight IST for all configured groups.
- `GROUP_ID` remains supported, with `GROUP_IDS` available for additional groups.
- Optional sticker triggers remain configurable and disabled by default.
- MongoDB remains the source for the statistics query during this migration.
- Environment-based configuration, Docker deployment, and operational logging remain available.

The behavior is migrated, not copied into the new domain layer. The message handler becomes an OpenWA inbound event adapter, the statistics request becomes a validated PBBot operation, the MongoDB read moves behind a service/repository boundary, and the scheduled broadcast becomes an idempotent worker operation. The migrated behavior must be independently testable before new community-management workflows are added.

### Migration acceptance criteria

- A message delivered by OpenWA containing exactly `!stats` produces the same statistics response as the existing bot for an allowed group.
- A nightly job sends the same statistics snapshot to every configured group, without duplicate sends when a job is retried.
- Existing `GROUP_ID` deployments continue to work; `GROUP_IDS` supports multiple destinations.
- Sticker behavior remains opt-in and does not affect statistics handling.
- The migrated handler does not query MongoDB directly and does not bypass the operation/audit boundary.
- Inbound message IDs, operation outcomes, scheduled-run IDs, and outbound OpenWA message IDs are traceable.
- Tests cover exact trigger matching, group allow-listing, multiple groups, scheduled execution, duplicate delivery, MongoDB failure, and OpenWA failure.

## 6. Personas and permissions

### Member

A community participant who needs a clear view of their assigned events and tasks.

Members may:

- View their assigned events and tasks.
- Submit and edit updates for their own assignments.
- View their own update and assignment history.
- View permitted event/status information.

Members may not create, delete, assign, or administer records.

### Admin

A community operator responsible for configuration and follow-up.

Admins may manage users, labels, events, schemas, tasks, assignments, reminders, reports, system configuration, and audit history.

Authorization must be enforced server-side for every operation; WhatsApp commands are not a security boundary.

## 7. Core concepts

| Entity | Purpose | Important relationships |
|---|---|---|
| User | Community member or admin | Has many labels and assignments |
| Label | Filterable user classification | Many-to-many with users and events |
| Event | Top-level program or activity | Has a type, optional schema, and assignments |
| Event schema | Dynamic fields for participation data | Belongs to a participation event |
| Task | Trackable work item in an organization event | Belongs to an event; may have an assignee |
| Assignment | Connects a user to an event or task | Stores status, reminders, missed count, and last update |
| Update | Progress record against an assignment field | Belongs to an assignment and actor |
| Audit log | Immutable record of an operation | Stores actor, operation, result, and timestamp |

### Event types

- **Participation:** structured participant data collected through an event schema. Examples: GSoC, LFX, Hacktoberfest, and research programs.
- **Organization:** work managed through assignable tasks. Examples: recruitment, hackathons, workshops, and bootcamps.

## 8. Primary user journeys

### 7.1 Member checks and updates work

1. Member sends `/my` or `/events`.
2. Router identifies the WhatsApp user and loads their active assignments.
3. Bot returns assigned events/tasks, status, and relevant due dates.
4. Member sends `/update` and selects an assignment and field.
5. Bot validates ownership, field type, and value.
6. Bot persists the update and audit record, then confirms the new status.

### 7.2 Admin creates a participation event

1. Admin creates an event with type `participation`.
2. Admin creates a schema and fields for that event.
3. Admin assigns the event directly or by labels.
4. Members submit values through guided `/update` flows.
5. Admin uses reports to review progress and pending fields.

### 7.3 Admin manages an organization event

1. Admin creates an event with type `organization`.
2. Admin creates tasks with due dates and priorities.
3. Admin assigns tasks to members.
4. Members submit progress updates.
5. Reminder runs notify members with overdue or missing updates.
6. Admin views pending, progress, and completed reports.

### 7.4 Natural-language request

1. User sends a natural-language message through WhatsApp.
2. OpenWA receives the WhatsApp event and delivers it to PBBot through its authenticated webhook/API boundary.
3. PBBot normalizes the OpenWA payload into an application-level message.
4. Interpreter converts it to a proposed operation or operation sequence.
5. System validates operation names, payloads, authorization, and referenced records.
6. Ambiguous or high-impact requests require confirmation.
7. Only validated and confirmed operations execute.
8. The complete request and result are audited.

## 9. Functional requirements

### FR-0 Existing bot compatibility

- The first deliverable shall migrate the existing `!stats`, scheduled broadcast, multi-group configuration, and optional sticker behavior into the OpenWA + PBBot architecture.
- The migrated behavior shall use the same OpenWA adapter, operation validator, audit path, and worker conventions as later PBBot features.
- The migration shall preserve behavior before introducing new event-management behavior.

### FR-1 Identity and users

- The system shall identify a user by their WhatsApp identifier.
- Admins shall create, update, deactivate, list, and assign roles to users.
- Deactivation shall preserve historical assignments, updates, and audit logs.
- Roles shall be `member` or `admin` in v1.

### FR-2 Labels

- Admins shall create, update, delete, and list labels.
- A user may have multiple labels.
- Admins shall assign and remove labels from users.
- Labels shall be usable for event targeting and filtering.

### FR-3 Events

- Admins shall create, edit, delete, list, assign, unassign, and change status for events.
- An event shall contain name, type, description, labels, start date, end date, and status.
- Event type shall be immutable after creation unless no dependent data exists.
- Deletion should be soft deletion when assignments or updates exist.

### FR-4 Dynamic schemas

- Admins shall create, edit, delete, and inspect schemas for participation events.
- Supported field types shall be text, number, boolean, date, URL, single select, multi select, and list.
- A field shall have a stable key, display name, type, required flag, and validation configuration.
- Submitted values shall be validated against the schema version active at submission time.
- Removing a field shall not erase historical update values.

### FR-5 Tasks

- Admins shall create, edit, delete, assign, complete, and list tasks.
- A task shall contain event, name, description, assignee, due date, priority, and status.
- A task may be completed only by its assignee or an admin.
- Task status transitions shall be validated and audited.

### FR-6 Assignments and updates

- The system shall represent event and task assignments explicitly.
- An assignment shall track status, reminder state, missed reminder count, and last update time.
- Members shall submit updates only for assignments they own.
- Members shall edit their own updates; admins may correct updates with an audit reason.
- Update history shall be append-only from the audit perspective.

### FR-7 Reminders

- Admins shall configure reminder frequency, active windows, and escalation threshold.
- A scheduled reminder run shall find eligible assignments idempotently.
- Each reminder attempt shall be recorded with assignment, attempt time, channel, and result.
- Missed reminders shall increment per-assignment counters.
- Escalation shall notify the configured admin/channel after the threshold is reached.

### FR-8 Reports

- Admins shall generate progress, pending, and completed reports.
- Reports shall support filtering by event, task, label, assignee, status, and date range.
- Report data shall be derived from persisted records at generation time.
- Report generation shall not mutate operational state except for its audit record.

### FR-9 Audit

- Every operation attempt shall produce an audit record, including rejected attempts.
- An audit record shall include actor, role, source, operation name, sanitized payload, result, error code if any, and timestamp.
- Audit records shall be immutable to application users.
- Admins shall list and filter audit records.

## 10. Operation catalogue

All state changes must use this operation naming scheme:

```text
user.create        user.update         user.delete         user.list
label.create       label.update        label.delete        label.assign
label.remove       label.list
event.create       event.update        event.delete        event.list
event.assign       event.unassign      event.status
schema.create      schema.update       schema.delete       schema.fields
task.create        task.update         task.delete         task.assign
task.complete      task.list
update.submit      update.edit         update.history
reminder.run       reminder.config     reminder.history
report.generate    report.progress     report.pending      report.completed
audit.list         audit.filter
stats.snapshot     stats.broadcast
```

`stats.snapshot` replaces the legacy `!stats` handler as a validated read operation. `stats.broadcast` is a system-scheduled operation that sends the snapshot to the configured groups. Both operations must record an audit event and OpenWA delivery result where applicable.

### Operation envelope

```json
{
  "name": "task.create",
  "payload": {},
  "actorId": "user-id",
  "source": "command"
}
```

`source` shall be one of `command`, `natural-language`, or `system`. Every operation must pass payload validation, authorization, resource validation, and business-rule validation before execution.

## 11. WhatsApp interface

### Member commands

```text
/help     /my       /events    /tasks
/update   /history  /status
```

### Admin commands

```text
/users    /labels   /events    /schema
/tasks    /assign   /reminders /reports
/audit
```

Multi-step commands should use numbered selections and explicit confirmation for destructive or bulk operations. The bot shall return a useful error and usage example for invalid commands.

## 12. System architecture

```text
WhatsApp user
      |
      v
OpenWA session / WhatsApp engine
      |
      +---- authenticated inbound webhook ----+
      |                                       |
      +<--- authenticated outbound API -------+
                                              v
                                   PBBot WhatsApp adapter
                                              |
                         +--------------------+--------------------+
                         |                                         |
                   Command parser                    NL interpreter (optional)
                         |                                         |
                         +--------------------+--------------------+
                                              v
                                      Operation generator
                                              |
                                              v
                                      Validator + RBAC
                                              |
                                              v
                                      Business services
                                              |
                         +--------------------+--------------------+
                         |                                         |
                       MongoDB                              Immutable audit log
```

### OpenWA integration contract

OpenWA owns WhatsApp connectivity. PBBot owns community operations. The boundary must be explicit:

| Direction | OpenWA responsibility | PBBot responsibility |
|---|---|---|
| Inbound | Maintain session, receive WhatsApp message, deliver signed webhook event, expose message metadata/media references. | Verify signature, deduplicate event, normalize payload, identify actor, route message. |
| Outbound | Send text/media/reaction through the configured session and return provider message ID/status. | Render command responses/reminders, request delivery, persist delivery result and audit event. |
| Session | QR, authentication, connection state, reconnect, session health. | Check session readiness and surface actionable errors to admins. |
| Operational | API-key auth, rate limiting, gateway audit/health, queue hooks where enabled. | Domain RBAC, operation validation, business rules, domain audit, reports. |

The PBBot adapter shall support:

- OpenWA base URL, API key, and session identifier/name from configuration.
- Inbound OpenWA message webhook events with HMAC verification.
- A stable normalized message shape: event ID, OpenWA session ID, WhatsApp sender/chat ID, message ID, timestamp, text, quoted-message reference, media reference, and raw-event correlation ID.
- Outbound text replies and reminder messages through OpenWA.
- Provider message IDs and delivery acknowledgements where available.
- Idempotency keyed by OpenWA event/message ID so webhook retries cannot execute an operation twice.
- A health check that distinguishes PBBot health, OpenWA API reachability, and WhatsApp session readiness.

PBBot shall not import OpenWA database tables or depend on OpenWA’s internal persistence schema. It shall use OpenWA’s public API/webhook contract.
```

The LLM, if enabled, may receive the user message and operation definitions, but shall not receive database credentials, execute queries, or call repositories directly.

## 13. Technical requirements

- Backend: Node.js, TypeScript, NestJS.
- Database: MongoDB with Mongoose.
- Messaging: OpenWA self-hosted WhatsApp API gateway. PBBot communicates with OpenWA through authenticated REST/webhook APIs; PBBot does not connect directly to WhatsApp or use the WhatsApp Cloud API.
- Scheduler: cron-backed reminder worker.
- Deployment: Docker-compatible; Railway/Render for initial deployment, Kubernetes-compatible configuration later.
- Configuration: environment variables with startup validation; secrets must not be committed.
- API: JSON operation endpoint for internal adapters and health endpoint for deployment checks.

## 14. Non-functional requirements

### Security

- Validate and authorize every operation server-side.
- Verify OpenWA webhook HMAC signatures before processing an inbound event.
- Redact tokens, credentials, and sensitive payload fields from audit logs.
- Use least-privilege database credentials and encrypted transport in deployed environments.

### Reliability

- Operation execution and audit persistence should be atomic where supported.
- Reminder runs must be idempotent and safe to retry.
- WhatsApp delivery failures must not silently mark reminders as delivered.
- Soft deletion must preserve history.

### Performance

- Typical command responses should complete within 2 seconds excluding WhatsApp network latency.
- List/report queries shall be paginated or bounded.
- Frequently filtered fields shall be indexed.

### Observability

- Log request correlation ID, operation name, actor ID, duration, and outcome.
- Do not log message bodies or secrets by default.
- Expose health checks for application and database connectivity.

## 15. MVP acceptance criteria

The MVP is ready for pilot use when:

- The existing `!stats` response and nightly configured-group broadcast work through OpenWA and the PBBot operation/audit path with parity tests.
- A member can be created and identified from a WhatsApp ID.
- An admin can create an event, create its schema or tasks, and assign members.
- A member can view assignments and submit a validated update.
- A reminder job can identify pending assignments and record delivery outcomes.
- An admin can view progress and pending reports.
- Member/admin authorization is enforced for every supported operation.
- Successful and rejected operations appear in immutable audit history.
- The service starts from documented environment variables and runs against MongoDB in Docker or a local environment.
- Automated tests cover operation validation, role restrictions, schema field validation, assignment ownership, and reminder idempotency.

## 16. Work packages

### Phase 0 — Design and decisions

Finalize data model, operation payload schemas, status transitions, OpenWA session ownership/configuration, and pilot event.

### Phase 1 — Existing bot migration

Implement `stats.snapshot` and `stats.broadcast` behind the OpenWA adapter. Preserve exact `!stats` matching, configured group allow-listing, `GROUP_ID`/`GROUP_IDS` compatibility, midnight IST scheduling, optional sticker triggers, and existing MongoDB statistics reads. Add idempotency, audit records, delivery tracking, failure handling, and parity tests against the existing bot behavior.

Reference implementation patterns: `media_automata/whatsapp/normalizer.py:44` for inbound normalization, `media_automata/whatsapp/client.py:61` for OpenWA text delivery, `media_automata/orchestrator.py:124` for message orchestration, `media_automata/repository.py:create_job()` for duplicate protection, and `media_automata/tests/test_repository.py` for retry/concurrency coverage.

### Phase 2 — Core platform

Implement users, labels, events, schemas, tasks, assignments, updates, operation validation, RBAC, audit logs, and test fixtures.

### Phase 3 — WhatsApp workflows and reminders

Implement webhook handling, command router, guided multi-step flows, outbound messaging, cron reminders, escalation, and delivery history.

### Phase 4 — Reporting and natural language

Implement report commands, safe operation generation, confirmation flows, confidence/ambiguity handling, and evaluation cases.

### Phase 5 — Pilot and hardening

Run one real event, monitor failure modes, document operations, add indexes/backups, and finalize deployment/runbook.

## 17. Open decisions before implementation

- Which OpenWA session/phone number will own the PBBot bot, and where will OpenWA be deployed?
- Which OpenWA webhook events and outbound API endpoints will be the supported PBBot integration contract?
- Is the pilot a participation event or an organization event?
- What exact statuses and transition rules should events, tasks, assignments, and updates use?
- Which fields are sensitive and must be redacted from audit logs?
- What reminder timezone, sending window, and escalation destination should be used?
- Which admin users are allowed to perform bulk assignments or destructive actions?
- Is natural language included in the MVP or deferred until command workflows are stable?

## 18. Reference assessment

The supplied reference repository is a useful example of a small WhatsApp bot with scheduled messaging and MongoDB access. Its documented scope is PBCTF registration-stat broadcasting via trigger words, so it should be treated as an integration/reference example rather than the product architecture for PBBot. PBBot requires a new operation-oriented domain layer, role-based authorization, dynamic schemas, assignments, reminders, reports, and auditing.

## 19. Existing internal foundations

Two existing projects in the workspace provide reusable implementation patterns and should be considered before building new infrastructure.

### 19.1 OpenWA

OpenWA is an existing NestJS/TypeScript WhatsApp API gateway. Its relevant capabilities include:

- WhatsApp session management and QR/session lifecycle handling.
- REST API and webhook delivery, including HMAC signature verification and webhook idempotency utilities.
- API-key authentication, permissions, rate limiting, health checks, audit logging, and structured error responses.
- Queue support through BullMQ/Redis, real-time events, Docker deployment, Swagger documentation, and graceful shutdown.
- Pluggable database, storage, cache, and WhatsApp engine adapters.
- SQLite for a low-dependency deployment and PostgreSQL for higher-scale deployments.

PBBot should prefer integrating with OpenWA through a small WhatsApp adapter rather than embedding a second WhatsApp engine. OpenWA’s transport and platform-management features do not replace PBBot’s users, labels, events, schemas, assignments, updates, reminder, report, or domain audit services.

### 19.2 media_automata

media_automata is an existing Python/FastAPI automation system using OpenWA. Its relevant patterns include:

- Normalization of OpenWA webhook payloads into an application-level incoming WhatsApp message.
- A command orchestrator that parses commands, replies to users, and coordinates persistence and workers.
- An optional LLM graph that converts messages into typed intents and platform tasks.
- Persistent jobs, per-task records, status transitions, retry scheduling, worker claims, heartbeats, and recovery of stale work.
- Idempotent handling of inbound messages and resilient external HTTP calls.
- Artifact storage abstractions, health/deployment checks, and a substantial test suite for commands, parsing, retries, scheduling, storage, and WhatsApp integration.

PBBot can reuse these patterns conceptually, and potentially reuse the OpenWA client/normalizer only if the team deliberately keeps a Python integration service. The preferred v1 architecture is a single NestJS PBBot service plus OpenWA, with typed operations as the shared contract. If media_automata remains a separate service, the operation envelope must be versioned and the service boundary must be explicit.

### 19.3 Reuse decision matrix

| Capability | Reuse from | PBBot decision |
|---|---|---|
| WhatsApp sessions, sending, inbound webhooks | OpenWA | Reuse via adapter/API |
| Webhook signature and duplicate-event handling | OpenWA + media_automata | Adopt as a mandatory integration requirement |
| Command parsing/orchestration patterns | media_automata | Reimplement around PBBot operations and RBAC |
| LLM structured intent parsing | media_automata | Reuse pattern only; output PBBot operations, never DB actions |
| Queue, retry, heartbeat, stale-work recovery | OpenWA + media_automata | Adopt for reminders and long-running reports |
| Storage abstraction and deployment checks | OpenWA + media_automata | Reuse patterns where needed |
| Users, events, schemas, assignments, updates | None | Build as PBBot domain modules |

### 19.4 Concrete media_automata precedents

The following are not abstract ideas; they are existing implementation references for the corresponding PBBot work. Paths are relative to `/home/unichronic/media_automata`.

| PBBot feature or step | Existing media_automata reference | What PBBot should take from it |
|---|---|---|
| Receive an OpenWA webhook | `src/media_automata/api.py:77-89`, `whatsapp/normalizer.py:44-89` | Accept gateway envelopes, normalize them once into a stable message object, and keep domain logic independent of OpenWA payload shapes. |
| Send a reply/reminder through OpenWA | `whatsapp/client.py:12-98`, `OpenWAClient.send_text()` | Hide OpenWA URL/session/API-key details behind a client interface; return provider results for delivery tracking. |
| Route a WhatsApp message | `orchestrator.py:124-156`, `process_whatsapp_message()` | Use one orchestration entry point that handles command routing, duplicate delivery, replies, and operation handoff. |
| Convert natural language to typed intent | `agents/graph.py:129-180`, `SocialAgentGraph.run()`; typed models in `schemas.py` | Produce typed proposed operations, validate them, and require confirmation for ambiguity; never let the model call repositories. |
| Persist operation/job state | `db/models.py:94-178`, `Job`, `PlatformTask`, `AgentMessage`, `AuditEvent` | Keep explicit lifecycle records and machine-readable results. PBBot maps this to operation, assignment, update, reminder, report, and audit collections. |
| Prevent duplicate webhook execution | `repository.py:create_job()` and `tests/test_repository.py:241-276` | Use the OpenWA message/event ID as an idempotency key before creating or executing an operation. |
| Claim work safely | `repository.py:347-390`, `claim_next_task()`; `tests/test_repository.py:206-239` | Make reminder/report workers claim records atomically so two workers cannot send or process the same work item. |
| Recover stuck work | `worker.py:84-98,274-286`, `repository.py:510-556` | Add heartbeats, stale-worker detection, cleanup, and recovery for reminder/report jobs. |
| Retry transient failures | `repository.py:395-420`, `retry.py`, `tests/test_retry.py` | Retry only transient errors with bounded backoff; preserve failure reason and audit every attempt. |
| Parse scheduled times | `scheduling.py:79-113`, `tests/test_scheduling.py` | Centralize timezone-aware parsing and avoid letting an LLM silently override an explicit command time. |
| Expose operational health | `api.py:45-60`, `monitoring.py:236-295` | Separate PBBot health, OpenWA reachability, session readiness, queue state, and stale work in deployment checks. |
| Test integration resilience | `tests/test_normalizer.py`, `test_whatsapp_client.py`, `test_orchestrator_resilience.py`, `test_repository.py` | Build fixtures for gateway payload variants, client failures, duplicate delivery, concurrency, retry, and recovery before pilot use. |

### 19.5 Architecture implication

```text
Member/Admin WhatsApp
        |
        v
      OpenWA  <---->  PBBot WhatsApp adapter
                         |
                         v
                  command/NL router
                         |
                         v
                  typed operations
                         |
                         v
                validation + RBAC + audit
                         |
                         v
                  PBBot domain services
                         |
                         v
                       MongoDB
```

The supplied `whatsapp-bot` repository remains a small reference for trigger-word handling and scheduled messaging. OpenWA and media_automata are the more relevant internal foundations for the production transport, orchestration, reliability, and operations patterns.
