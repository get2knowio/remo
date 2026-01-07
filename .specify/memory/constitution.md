<!--
  Sync Impact Report
  ===================
  Version change: 0.0.0 → 1.0.0 (initial adoption)

  Added principles:
  - I. Defensive Variable Access (Ansible)
  - II. Test All Conditional Paths
  - III. Idempotent by Default
  - IV. Fail Fast with Clear Messages
  - V. Documentation Reflects Reality

  Added sections:
  - Ansible-Specific Standards
  - Development Workflow
  - Governance

  Templates requiring updates:
  - plan-template.md: ⚠️ pending (Constitution Check section should reference these principles)
  - spec-template.md: ✅ no changes needed
  - tasks-template.md: ✅ no changes needed

  Follow-up TODOs: None
-->

# Remo Project Constitution

## Core Principles

### I. Defensive Variable Access (Ansible)

All Ansible registered variable attributes MUST use `| default()` filters when accessed in conditionals or templates.

**Rules:**
- NEVER access `.rc`, `.stdout`, `.stderr`, or `.stdout_lines` directly on registered variables
- ALWAYS use `variable.rc | default(1)` for return codes (default to failure)
- ALWAYS use `variable.stdout | default('')` for output strings
- When checking if a task ran successfully, use `variable.stdout is defined` instead of `variable is defined`

**Rationale:** When Ansible tasks are skipped, registered variables become dicts like `{"skipped": true}` without command result attributes. Direct attribute access causes "object of type 'dict' has no attribute" errors.

**Example - WRONG:**
```yaml
when: my_result.rc == 0
msg: "{{ my_result.stdout }}"
```

**Example - CORRECT:**
```yaml
when: my_result.rc | default(1) == 0
msg: "{{ my_result.stdout | default('N/A') }}"
```

### II. Test All Conditional Paths

Code with conditional logic MUST be tested under all possible conditions before committing.

**Rules:**
- For Ansible roles with `when:` conditions, test with conditions both true AND false
- For tasks that may be skipped, verify downstream tasks handle skipped variables
- Run playbooks against fresh systems AND systems with existing state
- Document which conditional paths were tested in commit messages or PR descriptions

**Rationale:** Bugs often hide in untested conditional branches. The "happy path" may work while error/skip paths fail silently or catastrophically.

### III. Idempotent by Default

All automation MUST be safely re-runnable without side effects.

**Rules:**
- Ansible tasks MUST use `changed_when` to accurately report changes
- Tasks MUST check existing state before making modifications
- Running the same playbook twice MUST produce identical end state
- Destructive operations MUST have explicit safeguards (confirmation, backup, etc.)

**Rationale:** Infrastructure automation runs in unpredictable environments. Users will re-run playbooks after failures, updates, or uncertainty.

### IV. Fail Fast with Clear Messages

Errors MUST be caught early with actionable guidance.

**Rules:**
- Validate prerequisites at the START of playbooks/roles (pre-flight checks)
- Error messages MUST explain: what failed, why it matters, and how to fix it
- Use `ansible.builtin.assert` for validation with detailed `fail_msg`
- Never swallow errors silently—use `failed_when: false` only with explicit error handling

**Rationale:** Users encountering errors need immediate, clear guidance. Cryptic failures waste time and erode trust.

### V. Documentation Reflects Reality

Documentation MUST be updated alongside code changes.

**Rules:**
- README changes MUST accompany feature changes
- Default behavior changes MUST be documented before merge
- Examples in documentation MUST be tested and working
- Remove or update documentation for deprecated features immediately

**Rationale:** Stale documentation is worse than no documentation—it actively misleads users.

## Ansible-Specific Standards

### Variable Handling Checklist

Before committing Ansible code, verify:

- [ ] All `.rc` accesses use `| default(1)`
- [ ] All `.stdout` accesses use `| default('')`
- [ ] All `when:` conditions handle skipped task variables
- [ ] All `debug` messages with variable interpolation use defaults
- [ ] Jinja2 templates in `msg:` blocks use safe attribute access

### Task Registration Pattern

```yaml
# Pattern for tasks that may be skipped
- name: Check something
  ansible.builtin.command: some_command
  register: check_result
  changed_when: false
  failed_when: false
  when: some_condition

# Safe usage of potentially-skipped result
- name: Use the result
  ansible.builtin.debug:
    msg: "Result: {{ check_result.stdout | default('skipped') }}"
  when: check_result.stdout is defined
```

## Development Workflow

### Pre-Commit Checklist

1. **Variable Safety**: Grep for `.rc ==` and `.stdout` without `| default`
2. **Conditional Coverage**: List all `when:` conditions and verify both branches tested
3. **Documentation Sync**: Diff README against code changes
4. **Idempotency Test**: Run playbook twice, verify no unexpected changes on second run

### Code Review Focus Areas

Reviewers MUST specifically check:
- Registered variable access patterns
- Error message clarity and actionability
- Documentation completeness
- Conditional path coverage

## Governance

This constitution establishes non-negotiable standards for the Remo project. All contributions MUST comply.

**Amendment Process:**
1. Propose changes via PR with rationale
2. Document what problem the amendment solves
3. Update all affected templates and documentation
4. Increment version appropriately

**Compliance:**
- PRs that violate principles MUST be revised before merge
- Existing code violations SHOULD be fixed when files are modified
- Constitution violations discovered in production are P1 bugs

**Version**: 1.0.0 | **Ratified**: 2026-01-06 | **Last Amended**: 2026-01-06
