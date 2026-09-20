"use client";

import { CONTEXT_POINTER_POLL_MS, contextHealthUrl, contextPublicationKey, loadContext, loadContextHealth } from "@/data/contextClient";
import type { ContextHealth, ContextManifest, ContextSource } from "@/data/contextSchema";
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";

type ApplyArgs = {
  manifest: ContextManifest;
  previous: ContextManifest | null;
  reason: "load" | "poll";
  now: number;
  previousNow: number;
};

export function useContextPolling({
  expectedSources,
  onChangedPublication,
  onSamePublication,
}: {
  expectedSources: ContextSource[];
  followingLiveRef: MutableRefObject<boolean>;
  onChangedPublication: (args: ApplyArgs) => void;
  onSamePublication: (args: ApplyArgs) => void;
}) {
  const [context, setContext] = useState<ContextManifest | null>(null);
  const [contextHealth, setContextHealth] = useState<ContextHealth | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [contextRetry, setContextRetry] = useState(0);
  const [healthRetry, setHealthRetry] = useState(0);
  const [displayNow, setDisplayNow] = useState(() => Date.now());
  const contextRef = useRef<ContextManifest | null>(null);
  const displayNowRef = useRef(displayNow);
  const visibleKeyRef = useRef("");
  contextRef.current = context;

  useEffect(() => {
    let cancelled = false;
    let latestRequest = 0;
    const apply = (manifest: ContextManifest, reason: "load" | "poll") => {
      if (cancelled) return;
      const now = Date.now();
      const nextKey = contextPublicationKey(manifest, now);
      const previous = contextRef.current;
      const args = { manifest, previous, reason, now, previousNow: displayNowRef.current } as const;
      if (reason === "poll" && previous && visibleKeyRef.current === nextKey) {
        onSamePublication(args);
        displayNowRef.current = now;
        setDisplayNow(now);
        return;
      }
      setContextError(null);
      onChangedPublication(args);
      setContext(manifest);
      visibleKeyRef.current = nextKey;
      displayNowRef.current = now;
      setDisplayNow(now);
    };
    const fetchContext = (reason: "load" | "poll") => {
      const request = ++latestRequest;
      return loadContext().then((manifest) => {
        if (request === latestRequest) apply(manifest, reason);
      }).catch((error: Error) => {
        if (cancelled || request !== latestRequest) return;
        if (reason === "poll" && contextRef.current) return;
        setContextError(error.message || "Unable to load wildfire-smoke forecast data.");
      });
    };
    void fetchContext("load");
    const poll = () => {
      if (document.visibilityState !== "hidden") void fetchContext("poll");
    };
    const interval = window.setInterval(poll, CONTEXT_POINTER_POLL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [contextRetry, onChangedPublication, onSamePublication]);

  const liveContextVersion = context?.mode === "live" ? context.version : null;
  const healthUrl = useMemo(
    () => liveContextVersion === null ? null : contextHealthUrl(liveContextVersion, expectedSources),
    [expectedSources, liveContextVersion],
  );
  useEffect(() => {
    if (liveContextVersion === null || healthUrl === null) {
      setContextHealth(null);
      setHealthError(null);
      return;
    }
    let cancelled = false;
    let latestRequest = 0;
    const poll = () => {
      if (document.visibilityState === "hidden") return;
      const request = ++latestRequest;
      void loadContextHealth(liveContextVersion, healthUrl)
        .then((health) => {
          if (cancelled || request !== latestRequest) return;
          setContextHealth(health);
          setHealthError(null);
        })
        .catch(() => {
          if (cancelled || request !== latestRequest) return;
          setHealthError("Publication health check unavailable.");
        });
    };
    setContextHealth(null);
    setHealthError(null);
    poll();
    const interval = window.setInterval(poll, CONTEXT_POINTER_POLL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [healthRetry, healthUrl, liveContextVersion]);

  const retryContext = useCallback(() => {
    setContextError(null);
    setContextRetry((value) => value + 1);
  }, []);
  const retryHealth = useCallback(() => {
    setHealthError(null);
    setHealthRetry((value) => value + 1);
  }, []);

  return {
    context,
    contextError,
    contextHealth,
    contextRef,
    contextRetry,
    displayNow,
    displayNowRef,
    healthError,
    retryContext,
    retryHealth,
    setDisplayNow,
  };
}
