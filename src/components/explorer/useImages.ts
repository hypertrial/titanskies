"use client";

import { closeContextImage, loadContextImage } from "@/data/contextClient";
import { useEffect, useRef, useState } from "react";

export type ImageSource = ImageBitmap | HTMLImageElement;

export function useImages(urls: string[], onError: (message: string | null) => void, retryKey: number, preferImageElement = false): Record<string, ImageSource> {
  const cache = useRef(new Map<string, ImageSource>());
  const previousRetryKey = useRef(retryKey);
  const [images, setImages] = useState<Record<string, ImageSource>>({});
  const signature = urls.join("|");
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const desired = [...new Set(urls.filter(Boolean))];
    if (previousRetryKey.current !== retryKey) {
      cache.current.forEach(closeContextImage);
      cache.current.clear();
      previousRetryKey.current = retryKey;
    }
    for (const [url, image] of cache.current) {
      if (!desired.includes(url)) { closeContextImage(image); cache.current.delete(url); }
    }
    setImages(Object.fromEntries(desired.flatMap((url) => {
      const image = cache.current.get(url);
      return image ? [[url, image]] : [];
    })));
    void Promise.all(desired.map(async (url) => {
      if (cache.current.has(url)) return;
      const image = await loadContextImage(url, controller.signal, { preferImageElement });
      if (cancelled) closeContextImage(image);
      else {
        cache.current.set(url, image);
        setImages((current) => ({ ...current, [url]: image }));
      }
    })).then(() => { if (!cancelled) onError(null); }).catch((error: unknown) => {
      if (!cancelled) onError(error instanceof Error ? error.message : "Unable to load a map layer.");
    });
    return () => { cancelled = true; controller.abort(); };
  // signature is a stable representation of the intended resident image window.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferImageElement, retryKey, signature]);
  useEffect(() => () => { cache.current.forEach(closeContextImage); cache.current.clear(); }, []);
  return images;
}
