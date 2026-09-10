"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { clearRecentCities, locationStorage, readRecentCities, searchCities, writeRecentCity } from "@/data/locationSearch";
import { COUNTRY_LABELS, type CityLabel } from "@/data/ui";
import { Icon } from "./Icon";

export function LocationSearch({ cities, compact = false, disabled, onOpen, onSelect }: { cities: CityLabel[]; compact?: boolean; disabled?: boolean; onOpen?: () => void; onSelect: (city: CityLabel, returnFocus?: HTMLElement | null) => void }) {
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [active, setActive] = useState(0);
  const [recents, setRecents] = useState<CityLabel[]>([]);
  const matches = useMemo(() => query.trim() ? searchCities(cities, query) : recents, [cities, query, recents]);

  useEffect(() => {
    if (typeof window !== "undefined") setRecents(readRecentCities(locationStorage(), cities));
  }, [cities]);
  useEffect(() => setActive(0), [query, open]);
  useEffect(() => {
    if (!open || !root.current) return;
    const element = root.current;
    const viewport = window.visualViewport;
    let frame = 0;
    const updateAvailableHeight = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const viewportBottom = viewport ? viewport.offsetTop + viewport.height : window.innerHeight;
        const available = Math.max(96, Math.floor(viewportBottom - element.getBoundingClientRect().bottom - 16));
        element.style.setProperty("--location-menu-max-height", `${available}px`);
      });
    };
    updateAvailableHeight();
    viewport?.addEventListener("resize", updateAvailableHeight);
    viewport?.addEventListener("scroll", updateAvailableHeight);
    window.addEventListener("resize", updateAvailableHeight);
    return () => {
      window.cancelAnimationFrame(frame);
      viewport?.removeEventListener("resize", updateAvailableHeight);
      viewport?.removeEventListener("scroll", updateAvailableHeight);
      window.removeEventListener("resize", updateAvailableHeight);
      element.style.removeProperty("--location-menu-max-height");
    };
  }, [open]);
  useEffect(() => {
    if (!compact || !expanded) return;
    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node)) return;
      setOpen(false);
      setExpanded(false);
      window.requestAnimationFrame(() => trigger.current?.focus());
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [compact, expanded]);

  const choose = (city: CityLabel) => {
    if (typeof window !== "undefined") {
      writeRecentCity(locationStorage(), city);
      setRecents(readRecentCities(locationStorage(), cities));
    }
    setQuery(city.name);
    setOpen(false);
    if (compact) setExpanded(false);
    input.current?.blur();
    onSelect(city, compact ? trigger.current : input.current);
  };
  const clear = () => { setQuery(""); setOpen(true); input.current?.focus(); };
  const clearHistory = () => {
    if (typeof window !== "undefined") clearRecentCities(locationStorage());
    setRecents([]);
    input.current?.focus();
  };

  const collapse = (restoreFocus = false) => {
    setOpen(false);
    if (!compact) return;
    setExpanded(false);
    if (restoreFocus) window.requestAnimationFrame(() => trigger.current?.focus());
  };

  return <div
    ref={root}
    className={`location-search${compact ? " compact" : ""}${expanded ? " expanded" : ""}${open ? " open" : ""}`}
    onBlur={(event) => {
      if (root.current?.contains(event.relatedTarget)) return;
      collapse(compact && !(event.relatedTarget instanceof HTMLElement));
    }}
  >
    {compact ? <button ref={trigger} className="location-search-trigger icon-button" type="button" disabled={disabled} onClick={() => { setExpanded(true); onOpen?.(); window.requestAnimationFrame(() => input.current?.focus()); }} aria-label="Search cities" aria-expanded={expanded} aria-controls={`${listId}-input`}><Icon name="search" /></button> : null}
    {!compact || expanded ? <div className="location-search-field">
      <Icon name="search" />
      <label className="sr-only" htmlFor={`${listId}-input`}>Search North American cities</label>
      <input
        ref={input}
        id={`${listId}-input`}
        type="search"
        value={query}
        disabled={disabled}
        placeholder="Search major cities"
        role="combobox"
        autoComplete="off"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && matches[active] ? `${listId}-${active}` : undefined}
        onFocus={() => {
          if (input.current?.dataset.suppressFocusOpen === "true") {
            delete input.current.dataset.suppressFocusOpen;
            return;
          }
          setOpen(true);
          onOpen?.();
        }}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); onOpen?.(); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); collapse(true); return; }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setOpen(true);
            if (matches.length) setActive((value) => (value + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length);
            return;
          }
          if (event.key === "Enter" && open && matches[active]) { event.preventDefault(); choose(matches[active]); }
        }}
        data-testid="location-search-input"
      />
      {query ? <button type="button" className="location-search-clear" onClick={clear} aria-label="Clear location search"><Icon name="close" /></button> : null}
    </div> : null}
    {open && (!compact || expanded) ? <div className="location-search-menu panel">
      <div className="location-search-menu-heading">
        <span>{query.trim() ? `${matches.length} ${matches.length === 1 ? "location" : "locations"}` : recents.length ? "Recent searches" : "Search by city"}</span>
        {!query.trim() && recents.length ? <button type="button" onClick={clearHistory} aria-label="Clear recent searches">Clear</button> : null}
      </div>
      <ul id={listId} role="listbox" aria-label="Location results">
        {matches.map((city, index) => <li
          key={`${city.country}-${city.region}-${city.searchName}`}
          id={`${listId}-${index}`}
          className="location-search-option"
          role="option"
          aria-selected={index === active}
          onMouseDown={(event) => event.preventDefault()}
          onMouseEnter={() => setActive(index)}
          onClick={() => choose(city)}
        ><span><strong>{city.name}</strong><small>{city.region}, {COUNTRY_LABELS[city.country]}</small></span></li>)}
      </ul>
      {!matches.length ? <p className="location-search-empty" role="status">{query.trim()
        ? `No match in the ${cities.length} curated major cities.`
        : `Search ${cities.length} curated city centers. Try Seattle, Montréal, or Mexico City.`}</p> : null}
    </div> : null}
  </div>;
}
