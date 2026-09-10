import type { ContextSelection } from "./contextSchema";

export type LayerVisibility = {
  air: boolean;
  forecast: boolean;
  incidents: boolean;
};

export type MapSelection = ContextSelection;

export type GeoLabel = {
  name: string;
  lon: number;
  lat: number;
};

export type CityLabel = GeoLabel & {
  searchName: string;
  region: string;
  country: "CAN" | "USA" | "MEX";
  priority: 1 | 2;
  mobile: boolean;
};

export type MapLabelTier =
  | "overview"
  | "primary"
  | "secondary"
  | "detail-major"
  | "detail-regional"
  | "detail-local"
  | "local";

type MapLabelBase = GeoLabel & {
  id: string;
  country: CityLabel["country"];
  tier: MapLabelTier;
  collisionRank: number;
};

export type CityMapLabel = MapLabelBase & {
  kind: "city";
};

export type LandmarkMapLabel = MapLabelBase & {
  kind: "landmark";
  category: "natural" | "park" | "cultural";
};

export type MapPlaceLabel = CityMapLabel | LandmarkMapLabel;

export const COUNTRY_LABELS: Record<CityLabel["country"], string> = {
  CAN: "Canada",
  USA: "United States",
  MEX: "Mexico",
};

export type GeoContext = {
  version: 3;
  source: {
    name: string;
    vectorVersion: string;
    rasterVersion: string;
    license: string;
    url: string;
    commit: string;
    sha256: Record<string, string>;
    landmarks: {
      name: "Wikidata";
      catalogVersion: "landmarks-v1";
      license: "CC0 1.0";
      url: string;
      manifest: string;
    };
  };
  contextBounds: [number, number, number, number];
  displayBounds?: [number, number, number, number];
  coastlines: number[][][];
  countryBorders: number[][][];
  regionBorders: number[][][];
  countryLabels: GeoLabel[];
  cities: CityLabel[];
  mapLabels: MapPlaceLabel[];
};
