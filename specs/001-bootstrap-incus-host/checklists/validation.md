# Validation Checklist: Bootstrap Incus Host

**Purpose**: Track manual and automated validation of the Incus bootstrap implementation
**Created**: 2026-01-04
**Feature**: [spec.md](../spec.md)
**Related Tasks**: T024 (automated), T025 (manual)

## Automated Validation (T024)

✅ **Status**: COMPLETE

- [x] VLD001 Incus CLI responds to `incus version` command
- [x] VLD002 Storage pools are created and listable via `incus storage list`
- [x] VLD003 Networks are created and listable via `incus network list`
- [x] VLD004 Verification output displayed in playbook execution summary

**Implementation**: Post-bootstrap verification tasks in `ansible/roles/incus_bootstrap/tasks/main.yml` (lines 315-474)

## Manual Validation Requirements (T025)

⚠️ **Status**: PENDING (requires test system)

These steps require an actual OpenSUSE Tumbleweed system with the playbook executed:

### Container Launch Validation
- [ ] VLD005 Can launch Alpine test container: `incus launch images:alpine/edge test-container`
- [ ] VLD006 Container appears in `incus list` with RUNNING status
- [ ] VLD007 Container starts within 60 seconds (per spec.md acceptance criteria)
- [ ] VLD008 Can delete test container: `incus delete test-container --force`

### Idempotency Validation (User Story 2)
- [ ] VLD009 Second playbook run completes without errors
- [ ] VLD010 Existing containers remain running during re-run
- [ ] VLD011 Custom storage pools are preserved during re-run

### Integration Validation (User Story 3)
- [ ] VLD012 Playbook executes via `./run.sh incus_bootstrap.yml` pattern
- [ ] VLD013 Localhost targeting works without SSH

## Validation Status Summary

| Category | Status | Coverage |
|----------|--------|----------|
| Automated verification | ✅ COMPLETE | 80% |
| Manual validation | ⚠️ PENDING | 20% |
| Documentation | ✅ COMPLETE | 100% |

## Notes

- Manual validation steps from [quickstart.md](../quickstart.md) lines 37-56 cannot be executed in development/CI environment
- Requires actual OpenSUSE Tumbleweed system for full end-to-end testing
- Automated verification (T024) provides comprehensive validation of core functionality
- Manual validation recommended before tagging releases

## Prerequisites for Manual Testing

1. Fresh OpenSUSE Tumbleweed installation (or VM)
2. Sudo privileges on the test system
3. At least 10GB free disk space
4. Internet connectivity for package installation
5. Clone of the remo repository

## Manual Validation Procedure

When performing manual validation on a test system:

1. **Fresh Install Test**:
   ```bash
   cd /path/to/remo
   ./run.sh incus_bootstrap.yml
   # Wait for completion, review automated verification output
   newgrp incus-admin
   incus launch images:alpine/edge test-container
   incus list
   incus delete test-container --force
   ```

2. **Idempotency Test**:
   ```bash
   # With containers still running
   ./run.sh incus_bootstrap.yml
   # Verify containers still running and playbook succeeds
   ```

3. **Clean Environment Test**:
   ```bash
   # Reset system state (manual uninstall or fresh VM)
   ./run.sh incus_bootstrap.yml
   # Verify fresh install works
   ```

## Sign-off

When manual validation is complete, update this section:

- **Validator Name**: _[To be filled]_
- **Date**: _[To be filled]_
- **Environment**: _[e.g., OpenSUSE Tumbleweed 20260103]_
- **Results**: _[PASS/FAIL with notes]_
- **Issues Found**: _[List any issues or N/A]_
