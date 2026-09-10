import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("http://127.0.0.1:8080"),
  title: "TitanSkies Smoke Forecast",
  description:
    "Explore a continuous North America smoke outlook, official PM2.5 AQI monitor readings, and agency-reported wildfires.",
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#02070c",
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
