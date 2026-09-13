import { describe, expect, it } from "vitest";
import { isNativeTitanSkiesShell, NATIVE_SHELL_UA_TOKEN } from "./useExplorerEnvironment";

describe("native TitanSkies shell detection", () => {
  it("recognizes only the native-app user agent token", () => {
    expect(isNativeTitanSkiesShell(`Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) ${NATIVE_SHELL_UA_TOKEN}0.1.0`)).toBe(true);
    expect(isNativeTitanSkiesShell("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko)")).toBe(false);
    expect(isNativeTitanSkiesShell("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")).toBe(false);
    expect(isNativeTitanSkiesShell("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")).toBe(false);
    expect(isNativeTitanSkiesShell("NotTitanSkiesNative/0.1.0")).toBe(false);
  });
});
