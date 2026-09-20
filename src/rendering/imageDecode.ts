export async function decodeBitmap(blob: Blob, signal: AbortSignal): Promise<ImageBitmap> {
  const pending = createImageBitmap(blob, { premultiplyAlpha: "none" });
  pending.then((image) => { if (signal.aborted) image.close(); }, () => undefined);
  if (signal.aborted) throw signal.reason;
  let onAbort: (() => void) | undefined;
  try {
    const image = await Promise.race([
      pending,
      new Promise<never>((_, reject) => {
        onAbort = () => reject(signal.reason);
        signal.addEventListener("abort", onAbort, { once: true });
      }),
    ]);
    if (signal.aborted) {
      image.close();
      throw signal.reason;
    }
    return image;
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
}
