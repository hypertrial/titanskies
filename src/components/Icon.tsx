export type IconName = "play" | "pause" | "previous" | "next" | "reset" | "layers" | "info" | "legend" | "close" | "search" | "ranking" | "more" | "install";

export function Icon({ name }: { name: IconName }) {
  const paths = {
    play: <path d="m9 7 8 5-8 5Z" />,
    pause: <><path d="M9 7h2v10H9z" /><path d="M14 7h2v10h-2z" /></>,
    previous: <><path d="M7 6h2v12H7z" /><path d="m17 7-7 5 7 5Z" /></>,
    next: <><path d="M15 6h2v12h-2z" /><path d="m7 7 7 5-7 5Z" /></>,
    reset: <><path d="M5 7v5h5" /><path d="M6.4 16.5a7 7 0 1 0-.5-8.2L5 10" /></>,
    layers: <><path d="m12 4 8 4-8 4-8-4Z" /><path d="m4 12 8 4 8-4" /><path d="m4 16 8 4 8-4" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6" /><path d="M12 7h.01" /></>,
    legend: <><path d="M5 17h14" /><path d="M5 12h14" /><path d="M8 7h8" /></>,
    close: <><path d="m7 7 10 10" /><path d="M17 7 7 17" /></>,
    search: <><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></>,
    ranking: <><path d="M5 18h14" /><path d="M7 15v-3" /><path d="M12 15V8" /><path d="M17 15V5" /></>,
    more: <><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></>,
    install: <><path d="M12 4v11" /><path d="m8 11 4 4 4-4" /><path d="M5 18h14v2H5z" /></>,
  } satisfies Record<IconName, React.ReactNode>;
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}
