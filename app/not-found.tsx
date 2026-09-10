import Link from "next/link";
import Image from "next/image";

export default function NotFound() {
  return (
    <main className="fallback-shell not-found-shell">
      <section className="fallback-card panel" aria-labelledby="not-found-title">
        <div className="fallback-brand" aria-label="TitanSkies Smoke Forecast">
          <Image src="/brand/logo-icon.png" alt="" width={28} height={28} priority unoptimized />
          <span><strong>TitanSkies</strong><small>Smoke Forecast</small></span>
        </div>
        <p className="eyebrow">TitanSkies Smoke Forecast · 404</p>
        <h1 id="not-found-title">This smoke-map page isn’t here.</h1>
        <p>The address may be outdated. Return to the North America smoke outlook to keep exploring current forecast and air-quality data.</p>
        <Link className="fallback-action" href="/">Return to the smoke outlook</Link>
      </section>
    </main>
  );
}
