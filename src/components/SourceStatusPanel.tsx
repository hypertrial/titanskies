"use client";

import { sourceFreshness } from "@/data/airQuality";
import { contextSourceLabel, type ContextHealth, type ContextSource, type SourceState } from "@/data/contextSchema";
import { PUBLIC_DATA_WARNING, formatExactInterfaceTime } from "@/data/interfacePresentation";

const STATE_LABELS: Record<SourceState["status"], string> = {
  ok: "Current",
  stale: "Stale",
  error: "Update failed",
  unavailable: "Unavailable",
};

const HEALTH_ISSUE_LABELS: Record<string, string> = {
  "initializing": "The first local data publication is still being prepared.",
  "storage-unavailable": "Publication storage could not be reached.",
  "invalid-status": "Publication status metadata is invalid.",
  "invalid-pointer": "The current publication pointer is invalid.",
  "invalid-manifest": "The current publication manifest is invalid.",
  "publication-unavailable": "Publication health data is unavailable.",
  "stale-heartbeat": "The scheduled data update is late.",
  "stale-forecast": "The latest complete forecast is stale.",
  "insufficient-forecast-coverage": "Forecast coverage is shorter than six hours.",
  "forecast-unavailable": "The integrated smoke forecast is unavailable.",
  "unexpected-version": "The published data version does not match this app.",
  "invalid-expected-version": "The health check configuration is invalid.",
  "invalid-expected-source": "The health check configuration is invalid.",
};

function healthIssueLabel(issue: string): string {
  const sourceIssue = /^source-([a-z]+)-(stale|error|unavailable|missing)$/.exec(issue);
  if (sourceIssue) {
    const plural = sourceIssue[1] === "wfigs" || sourceIssue[1] === "cwfis";
    const status = sourceIssue[2] === "missing" ? `${plural ? "were" : "was"} not published`
      : sourceIssue[2] === "error" ? "failed to update" : `${plural ? "are" : "is"} ${sourceIssue[2]}`;
    return `${contextSourceLabel(sourceIssue[1] as ContextSource)} ${status}.`;
  }
  return HEALTH_ISSUE_LABELS[issue] ?? "An automatic data-health check reported a problem.";
}

function overallLabel(mode: "live" | "demo", health: ContextHealth | null, healthError: string | null): string {
  if (mode === "demo") return "Demo publication";
  if (health?.status === "healthy") return health.publication === "retained" ? "Retained publication" : "Publication healthy";
  if (health?.status === "degraded") return health.publication === "fresh" ? "Active data partially degraded" : "Publication partially degraded";
  if (health?.status === "unhealthy") return "Publication stale";
  return healthError ? "Health verification unavailable" : "Checking publication";
}

function ExactTime({ value }: { value: string }) {
  return value ? <time dateTime={value}>{formatExactInterfaceTime(value)}</time> : <span>Unavailable</span>;
}

export function SourceStatusPanel({
  mode,
  health,
  healthError,
  publicationAt,
  sourceLabel,
  sourceAt,
  sources,
  referenceTime,
  onRetry,
  onMethodology,
}: {
  mode: "live" | "demo";
  health: ContextHealth | null;
  healthError: string | null;
  publicationAt: string;
  sourceLabel: "Guidance" | "Observed";
  sourceAt: string;
  sources: Array<[string, SourceState]>;
  referenceTime: number;
  onRetry: () => void;
  onMethodology: () => void;
}) {
  const stateClass = mode === "demo" ? "demo" : health?.status ?? (healthError ? "degraded" : "checking");
  const issues = [...new Set((health?.issues ?? []).map(healthIssueLabel))];
  const canRetry = mode === "live" && (Boolean(healthError) || Boolean(health && health.status !== "healthy"));
  return <section className="source-status-panel" data-testid="source-status-panel">
    <div className={`source-status-hero ${stateClass}`}>
      <i aria-hidden="true" />
      <span><strong>{overallLabel(mode, health, healthError)}</strong><small>{mode === "demo" ? "Deterministic forecast and observation fixtures" : publicationAt ? `Updated ${sourceFreshness({ status: "ok", checkedAt: publicationAt, observedAt: publicationAt, provenance: "publication", error: null }, referenceTime).replace(" old", " ago")}` : "Update time unavailable"}</small></span>
    </div>
    <dl className="source-status-summary">
      <div><dt>Publication</dt><dd><ExactTime value={publicationAt} /></dd></div>
      <div><dt>{sourceLabel}</dt><dd><ExactTime value={sourceAt} /></dd></div>
    </dl>
    <div className="source-status-providers">
      <h3>Active providers</h3>
      <ul>{sources.map(([label, state]) => <li key={label} data-status={state.status}>
        <i aria-hidden="true" />
        <span><strong>{label}</strong><small>{state.observedAt ? <>{sourceFreshness(state, referenceTime)} · <ExactTime value={state.observedAt} /></> : state.error ?? "No provider timestamp"}</small></span>
        <em>{STATE_LABELS[state.status]}</em>
      </li>)}</ul>
    </div>
    {healthError ? <p className="source-status-warning">Automatic update health could not be verified: {healthError}</p> : null}
    {issues.length ? <ul className="source-status-issues">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
    <p className="source-status-warning" data-testid="public-data-warning">{PUBLIC_DATA_WARNING}</p>
    <div className="source-status-actions">
      {canRetry ? <button className="text-button" type="button" onClick={onRetry}>Check again</button> : null}
      <button className="context-link" type="button" onClick={onMethodology}>About &amp; sources</button>
    </div>
  </section>;
}
