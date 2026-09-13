# Phased Scope Reduction Plan

## Objective

Keep LinuxCNC honest and maintainable by separating the public CNC GUI and LinuxCNC interface from the private machine-admin and deployment layer.

## Core Rule

LinuxCNC owns the real machine runtime and configuration. LinuxCNC provides the operator-facing interface and configuration assistance around LinuxCNC.

Anything that changes system identity, certificate trust, deployment, recovery, update, service lifecycle, or privileged config activation is private/admin scope and should not be part of the default public project path.

## Public Scope

The public path should include only the parts that are clearly part of the CNC GUI and LinuxCNC workflow:

- dashboard UI
- status and telemetry presentation
- G-code loading and management
- tool-table handling
- safe movement commands
- basic probing and touch-off routines that are locally bounded
- machine setup assistance that does not change privileged host state
- generic documentation and UI configuration

## Private Scope

The private path should include the machine-specific and privileged behavior:

- machine-side agent services
- TLS certificate and trust management
- fingerprint and role enforcement
- systemd and service deployment
- LinuxCNC owner helper processes
- recovery and rollback logic
- firmware flashing and update orchestration
- config activation and rollout management
- machine-specific deployment files and environment values

## Current repo mapping

The current repo already points to this split:

- Root README is mostly public app architecture and UI language
- agent README is explicitly private/admin-layer documentation
- The agent folder is clearly machine-admin scope
- The docs folder can contain the public/private boundary explanation

## Phase 1: Boundary Declaration

1. Confirm the public/private split in the main documentation.
2. Document the rule that LinuxCNC owns runtime config and LinuxCNC owns the UI + workflow layer.
3. Add scope notes to the root docs and maintainers guide.

## Phase 2: Isolation

1. Create the public and private folders as the declared structural boundary.
2. Keep public-facing project docs and UI modules in the public path.
3. Keep system/admin and service-specific logic in the private path.
4. Do not let the private path become the default runtime path for normal users.

## Phase 3: Runtime Safety

1. Remove machine-admin features from the default public startup path.
2. Gate them behind explicit opt-in or private configuration.
3. Keep app settings separate from LinuxCNC runtime configuration.

## Phase 4: Configuration Hygiene

1. Treat LinuxCNC INI/HAL files as machine runtime files, not app settings.
2. Do not mix app preferences and machine deployment config in one file.
3. Keep machine-specific addresses, usernames, service names, and paths out of the public repo.

## Phase 5: Public Release Readiness

1. Edit the main README to describe the public scope clearly.
2. Remove or redact real deployment details from public-facing docs.
3. Keep machine-specific deployment data in local/private configuration only.
4. Ship the public project as a generic CNC front-end rather than a full machine control platform.

## Guardrails

- No public release should imply system ownership over the host machine.
- No public feature set should include certificate, service, or lifecycle management unless it is intentionally private.
- The public repo should be generic enough to use without exposing real machine layout.
- The private repo may hold machine-specific values, but it should remain clearly marked as private implementation detail.

## Recommended sequence

1. Public/private boundary docs
2. Folder isolation
3. Gate private admin features
4. Clean up app config vs LinuxCNC config separation
5. Final public release trim

This is intentionally a controlled reduction rather than a full rewrite. The goal is to keep the project useful and honest without drifting into a machine-admin platform by default.
