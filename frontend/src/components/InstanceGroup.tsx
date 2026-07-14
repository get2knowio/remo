// One instance's heading (provider + name) + status badge, and either its
// error/remediation (non-`ok`) or its TargetCards (`ok`). Every discovered
// instance is rendered, including unreachable ones and ones with zero
// projects (US1: "unreachable instances remain visible with actionable
// status").
//
// T047 (FR-030): also carries an "Open all on instance" bulk-open control,
// and passes multi-select + single-open plumbing down to each `TargetCard`.

import type { DiscoveryInstance, SessionTarget } from "../api/client";
import { TargetCard } from "./TargetCard";
import "./InstanceGroup.css";

const PROVIDER_LABELS: Record<string, string> = {
  aws: "☁️ AWS",
  hetzner: "🌩️ Hetzner",
  incus: "📦 Incus",
  proxmox: "🖥️ Proxmox",
};

const STATUS_LABELS: Record<DiscoveryInstance["status"], string> = {
  ok: "OK",
  unreachable: "Unreachable",
  auth_failed: "Auth failed",
  no_remo_host: "Update required",
  incompatible_protocol: "Incompatible",
  malformed: "Malformed response",
  timeout: "Timed out",
};

function providerLabel(instanceType: string): string {
  return PROVIDER_LABELS[instanceType] ?? instanceType;
}

interface InstanceGroupProps {
  instance: DiscoveryInstance;
  targets: SessionTarget[];
  onRefresh: () => void;
  selectedIds: Set<string>;
  onToggleSelect: (targetId: string) => void;
  onOpenTarget: (target: SessionTarget) => void;
  onOpenAll: (targets: SessionTarget[]) => void;
}

export function InstanceGroup({
  instance,
  targets,
  onRefresh,
  selectedIds,
  onToggleSelect,
  onOpenTarget,
  onOpenAll,
}: InstanceGroupProps): JSX.Element {
  const isOk = instance.status === "ok";

  return (
    <section className={`instance-group instance-group--${instance.status}`}>
      <header className="instance-group-header">
        <span className="instance-group-title">
          {providerLabel(instance.instance_type)} <strong>{instance.instance_name}</strong>
        </span>
        <div className="instance-group-header-controls">
          {isOk && targets.length > 0 && (
            <button
              type="button"
              className="instance-group-open-all-button"
              data-testid={`open-all-instance-${instance.instance_id}`}
              onClick={() => onOpenAll(targets)}
            >
              Open all on instance ({targets.length})
            </button>
          )}
          <span className={`instance-status-badge instance-status-badge--${instance.status}`}>
            {STATUS_LABELS[instance.status]}
          </span>
        </div>
      </header>

      {!isOk && (
        <div
          className={
            instance.error?.retryable
              ? "instance-group-error instance-group-error--retryable"
              : "instance-group-error instance-group-error--fatal"
          }
        >
          <p className="instance-group-error-message">
            {instance.error?.message ?? "This instance could not be reached."}
          </p>
          <p className="instance-group-error-remediation">
            {instance.error?.remediation ?? "Check the instance configuration and try again."}
          </p>
          {instance.error?.retryable ? (
            <button type="button" onClick={onRefresh} className="instance-group-retry-button">
              Retry
            </button>
          ) : (
            // FR-059: no_remo_host / incompatible_protocol / malformed are
            // non-retryable — render the update remediation clearly instead
            // of a generic error / silent omission.
            <p className="instance-group-update-note">
              This instance needs its Remo host tools updated before it can be discovered.
            </p>
          )}
        </div>
      )}

      {isOk && (
        <div className="instance-group-targets">
          {targets.length === 0 ? (
            <p className="instance-group-empty">No projects found on this instance.</p>
          ) : (
            targets.map((target) => (
              <TargetCard
                key={target.id}
                target={target}
                isSelected={selectedIds.has(target.id)}
                onToggleSelect={onToggleSelect}
                onOpen={onOpenTarget}
              />
            ))
          )}
        </div>
      )}
    </section>
  );
}
