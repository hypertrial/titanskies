export function devConfiguration(args, environment = process.env) {
  const demo = args.includes("--demo-data");
  const localIngest = args.includes("--local-ingest");
  const mode = localIngest ? "local-ingest" : demo ? "demo" : "local";
  const env = {
    ...environment,
    NEXT_DIST_DIR: environment.NEXT_DIST_DIR || ".next-dev",
    CONTEXT_SOURCE: demo ? "demo" : "live",
    TITANSKIES_DATA_DIR: demo ? "public/demo" : (environment.TITANSKIES_DATA_DIR || ".local/data"),
    TITANSKIES_CACHE_DIR: environment.TITANSKIES_CACHE_DIR || ".local/cache",
    NEXT_PUBLIC_CONTEXT_URL: demo ? "/demo/context/latest.json" : "/api/context-data",
  };
  return { env, mode, watch: localIngest };
}
