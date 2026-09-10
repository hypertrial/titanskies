"use client";

import { useEffect, useState } from "react";

export type DeferredInstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

export function useExplorerEnvironment(onMotionReduced: () => void) {
  const [mobile, setMobile] = useState(false);
  const [shortLandscape, setShortLandscape] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [webgl, setWebgl] = useState(true);
  const [installPrompt, setInstallPrompt] = useState<DeferredInstallPrompt | null>(null);
  const [standalone, setStandalone] = useState(false);
  const [ios, setIos] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(true);
  const [networkOnline, setNetworkOnline] = useState(true);

  useEffect(() => {
    setWebgl(Boolean(document.createElement("canvas").getContext("webgl2")));
    const narrow = window.matchMedia("(max-width: 767px)");
    const compactLandscape = window.matchMedia("(max-height: 500px) and (orientation: landscape)");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => { setMobile(narrow.matches); setShortLandscape(compactLandscape.matches); };
    const updateMotion = () => {
      setReducedMotion(motion.matches);
      if (motion.matches) onMotionReduced();
    };
    update();
    updateMotion();
    narrow.addEventListener("change", update);
    compactLandscape.addEventListener("change", update);
    motion.addEventListener("change", updateMotion);
    return () => {
      narrow.removeEventListener("change", update);
      compactLandscape.removeEventListener("change", update);
      motion.removeEventListener("change", updateMotion);
    };
  }, [onMotionReduced]);

  useEffect(() => {
    const displayMode = window.matchMedia("(display-mode: standalone)");
    const navigatorWithStandalone = navigator as Navigator & { standalone?: boolean };
    const updateStandalone = () => setStandalone(displayMode.matches || navigatorWithStandalone.standalone === true);
    const onBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as DeferredInstallPrompt);
    };
    const onInstalled = () => {
      setStandalone(true);
      setInstallPrompt(null);
    };
    updateStandalone();
    setIos(/iPad|iPhone|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1));
    displayMode.addEventListener("change", updateStandalone);
    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      displayMode.removeEventListener("change", updateStandalone);
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  useEffect(() => {
    const updateNetwork = () => setNetworkOnline(navigator.onLine);
    const updateVisibility = () => setDocumentVisible(document.visibilityState === "visible");
    updateNetwork();
    updateVisibility();
    window.addEventListener("online", updateNetwork);
    window.addEventListener("offline", updateNetwork);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => {
      window.removeEventListener("online", updateNetwork);
      window.removeEventListener("offline", updateNetwork);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, []);

  return {
    documentVisible,
    installPrompt,
    ios,
    mobile,
    networkOnline,
    reducedMotion,
    setInstallPrompt,
    setStandalone,
    shortLandscape,
    standalone,
    webgl,
  };
}
