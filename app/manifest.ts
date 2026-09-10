import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "TitanSkies Smoke Forecast",
    short_name: "TitanSkies",
    description:
      "Explore a continuous North America smoke outlook, official PM2.5 AQI monitor readings, and agency-reported wildfires.",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#02070c",
    theme_color: "#02070c",
    icons: [
      { src: "/pwa-icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/pwa-icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
