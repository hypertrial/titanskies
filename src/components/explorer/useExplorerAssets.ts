"use client";

import { loadAirMonitors, loadIncidents, monitorIndexSystem } from "@/data/airQuality";
import type { AirQualityMonitor, ContextManifest, FireIncident } from "@/data/contextSchema";
import { useCallback, useEffect, useRef, useState } from "react";

export function useExplorerAssets({
  airDataNeeded,
  context,
  contextRetry,
  incidentsVisible,
}: {
  airDataNeeded: boolean;
  context: ContextManifest | null;
  contextRetry: number;
  incidentsVisible: boolean;
}) {
  const [airError, setAirError] = useState<string | null>(null);
  const [airRetry, setAirRetry] = useState(0);
  const [airReady, setAirReady] = useState(false);
  const [incidentError, setIncidentError] = useState<string | null>(null);
  const [incidents, setIncidents] = useState<FireIncident[]>([]);
  const [monitors, setMonitors] = useState<AirQualityMonitor[]>([]);
  const loadedAirPublicationRef = useRef("");

  useEffect(() => {
    setAirError(null);
    setAirReady(false);
    setIncidentError(null);
    setIncidents([]);
    setMonitors([]);
    loadedAirPublicationRef.current = "";
  }, [context?.generatedAt]);

  useEffect(() => {
    if (!context || !incidentsVisible) {
      setIncidentError(null);
      return;
    }
    let cancelled = false;
    setIncidentError(null);
    loadIncidents([context.fires.wfigsIncidentsUrl, context.fires.cwfisIncidentsUrl], (message) => {
      if (!cancelled) setIncidentError(message);
    })
      .then((value) => { if (!cancelled) setIncidents(value); })
      .catch((error: Error) => { if (!cancelled) setIncidentError(error.message); });
    return () => { cancelled = true; };
  }, [context, contextRetry, incidentsVisible]);

  useEffect(() => {
    if (!context || !airDataNeeded) return;
    const publicationKey = `${contextRetry}:${airRetry}:${context.generatedAt}:${JSON.stringify(context.air)}`;
    if (loadedAirPublicationRef.current === publicationKey) return;
    let cancelled = false;
    setAirReady(false);
    setAirError(null);
    setMonitors([]);
    loadAirMonitors(context, (message) => { if (!cancelled) setAirError(message); }, undefined, (value) => {
      if (!cancelled) setMonitors(value);
    })
      .then((value) => {
        if (cancelled) return;
        setMonitors(value);
        setAirReady(true);
        loadedAirPublicationRef.current = publicationKey;
      })
      .catch((error: Error) => { if (!cancelled) setAirError(error.message); });
    return () => { cancelled = true; };
  }, [airDataNeeded, airRetry, context, contextRetry]);

  const clearIncidentError = useCallback(() => setIncidentError(null), []);
  const retryAir = useCallback(() => setAirRetry((value) => value + 1), []);

  // City matching and rankings still wait for airReady and the complete snapshot.
  const airMapReady = airReady || monitors.some((monitor) => monitorIndexSystem(monitor) === "us-epa-pm25-aqi");
  return { airError, airMapReady, airReady, clearIncidentError, incidentError, incidents, monitors, retryAir };
}
