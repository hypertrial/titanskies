import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  {
    // The renderer intentionally mutates Three.js objects and refs outside
    // React state; keep the pre-upgrade lint contract for those imperative APIs.
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
  globalIgnores([".next/**", ".next-*/**", "node_modules/**", "public/demo/**", "ingest/**", "api/**", "scripts/**", "tests/python/**", "next-env.d.ts", ".local/**", "dist/**", "macos/TitanSkies/.build/**"]),
]);
