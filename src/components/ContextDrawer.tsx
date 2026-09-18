"use client";

import { isContextDrawerOutsideIgnored } from "@/data/explorerUi";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Icon } from "./Icon";

export type ContextDrawerCloseOrigin = "keyboard" | "pointer";

export function ContextDrawer({ title, eyebrow, children, onClose }: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
  onClose: (restoreFocus: boolean, origin: ContextDrawerCloseOrigin) => void;
}) {
  const id = useId();
  const root = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const onCloseRef = useRef(onClose);
  const [scrollMore, setScrollMore] = useState(false);
  onCloseRef.current = onClose;
  const updateScrollMore = useCallback(() => {
    const element = body.current;
    if (!element) return;
    setScrollMore(element.scrollTop + element.clientHeight < element.scrollHeight - 1);
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => heading.current?.focus());
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCloseRef.current(true, "keyboard");
    };
    const outside = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node)) return;
      const target = event.target instanceof Element ? event.target : null;
      if (isContextDrawerOutsideIgnored(target)) return;
      const transfersFocus = Boolean(target?.closest("button, a[href], input, select, textarea, [contenteditable='true'], [tabindex]:not([tabindex='-1'])"));
      onCloseRef.current(!transfersFocus, "pointer");
    };
    document.addEventListener("keydown", escape);
    document.addEventListener("pointerdown", outside);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", escape);
      document.removeEventListener("pointerdown", outside);
    };
  }, []);

  useEffect(() => {
    const element = body.current;
    if (!element) return;
    const frame = window.requestAnimationFrame(updateScrollMore);
    const resize = new ResizeObserver(updateScrollMore);
    resize.observe(element);
    for (const child of element.children) resize.observe(child);
    return () => {
      window.cancelAnimationFrame(frame);
      resize.disconnect();
    };
  }, [children, updateScrollMore]);

  return <aside
    ref={root}
    className="context-drawer panel"
    role="dialog"
    aria-modal="false"
    aria-labelledby={id}
    data-scroll-more={scrollMore ? "true" : "false"}
  >
    <div className="context-drawer-heading">
      <div>{eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}<h2 id={id} ref={heading} tabIndex={-1}>{title}</h2></div>
      <button className="icon-button quiet" type="button" onClick={(event) => onClose(true, event.detail === 0 ? "keyboard" : "pointer")} aria-label={`Close ${title}`}><Icon name="close" /></button>
    </div>
    <div ref={body} className="context-drawer-body" onScroll={updateScrollMore}>{children}</div>
    <span className="drawer-scroll-cue" aria-hidden="true" />
  </aside>;
}
