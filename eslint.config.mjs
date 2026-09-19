import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import globals from "globals";

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
  {
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: globals.node,
      sourceType: "module",
    },
  },
  globalIgnores([".next/**", ".next-*/**", "node_modules/**", "public/demo/**", "ingest/**", "api/**", "tests/python/**", "next-env.d.ts", ".local/**", "dist/**"]),
]);
