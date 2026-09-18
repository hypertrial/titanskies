import { expect, test, type Page } from "@playwright/test";

const knownViewportWarning = /^Viewport argument key ["']interactive-widget["'] not recognized and ignored\.?$/;

function watchBrowserErrors(page: Page) {
  const messages: string[] = [];
  page.on("pageerror", (error) => messages.push(error.message));
  page.on("console", (message) => {
    if (message.text().includes("interactive-widget")) {
      messages.push(message.text());
    } else if (message.type() === "error") {
      const source = message.location().url;
      messages.push(source ? `${message.text()} (${source})` : message.text());
    }
  });
  return () => expect(messages.filter((message) => !knownViewportWarning.test(message))).toEqual([]);
}

async function waitForReady(page: Page) {
  await expect(page.getByTestId("loading-state")).toBeHidden({ timeout: 20_000 });
  await expect(page.locator("canvas")).toHaveCount(1);
}

async function openMore(page: Page) {
  await page.getByRole("button", { name: "More options" }).tap();
  return page.getByRole("dialog", { name: "More" });
}

test.describe.configure({ mode: "serial" });

test("boots the portrait mobile experience without viewport overflow", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowserErrors(page);
  await page.goto("/");
  await waitForReady(page);

  await expect(page.getByTestId("source-badge")).toHaveAccessibleName("Demo smoke outlook");
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute("content", /interactive-widget=resizes-content/);
  const search = page.getByTestId("location-search-input");
  expect(await search.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth));
  assertNoBrowserErrors();
});

test("keeps search and compact selection sheets usable in WebKit", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowserErrors(page);
  await page.goto("/");
  await waitForReady(page);

  const search = page.getByTestId("location-search-input");
  await search.fill("a");
  const results = page.getByRole("listbox", { name: "Location results" }).locator("..");
  await expect(results).toBeVisible();
  expect(await page.getByRole("option").count()).toBeLessThanOrEqual(8);
  const resultBottom = await results.evaluate((element) => element.getBoundingClientRect().bottom);
  const visibleBottom = await page.evaluate(() => (window.visualViewport?.offsetTop ?? 0) + (window.visualViewport?.height ?? window.innerHeight));
  expect(resultBottom).toBeLessThanOrEqual(visibleBottom + 1);

  await search.fill("Toronto");
  await page.getByRole("option", { name: /Toronto/ }).tap();
  let sheet = page.getByRole("dialog", { name: "Toronto" });
  await expect(sheet).toBeVisible();
  const viewportHeight = await page.evaluate(() => window.innerHeight);
  expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(Math.min(240, viewportHeight * 0.38) + 1);
  await sheet.getByText("More details").tap();
  expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(viewportHeight * 0.6 + 1);
  expect(await sheet.locator(".context-drawer-body").evaluate((element) => getComputedStyle(element).overflowY)).toBe("auto");
  await sheet.getByRole("button", { name: "Close toronto" }).tap();
  await expect(page.getByTestId("interactive-globe")).toBeFocused();

  await search.fill("Toronto");
  await search.press("Enter");
  sheet = page.getByRole("dialog", { name: "Toronto" });
  await expect(sheet).toBeVisible();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(search).toBeFocused();
  assertNoBrowserErrors();
});

test("shows iOS installation guidance and hides it in standalone mode", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowserErrors(page);
  await page.goto("/");
  await waitForReady(page);

  let more = await openMore(page);
  await expect(more.getByTestId("install-action")).toContainText("Safari’s Share menu");
  await more.getByTestId("install-action").tap();
  const instructions = page.getByTestId("install-instructions");
  await expect(instructions).toContainText("Open TitanSkies in Safari");
  await expect(instructions).toContainText("Share");
  await expect(instructions).toContainText("Add to Home Screen");
  await expect(instructions).toContainText("needs a network connection");

  await page.addInitScript(() => Object.defineProperty(navigator, "standalone", { configurable: true, value: true }));
  await page.reload();
  await waitForReady(page);
  more = await openMore(page);
  await expect(more.getByTestId("install-action")).toHaveCount(0);
  assertNoBrowserErrors();
});

test("keeps short-landscape controls and drawers within the safe viewport", async ({ page }) => {
  const assertNoBrowserErrors = watchBrowserErrors(page);
  await page.setViewportSize({ width: 844, height: 390 });
  await page.goto("/");
  await waitForReady(page);

  await expect(page.getByRole("button", { name: "Search cities" })).toBeVisible();
  const dock = page.locator(".playback-dock");
  const legend = page.getByRole("button", { name: "Open smoke legend" });
  const [dockBox, legendBox, previousBox, playBox, nextBox] = await Promise.all([
    dock.boundingBox(),
    legend.boundingBox(),
    page.getByTestId("previous-frame").boundingBox(),
    page.getByTestId("play-toggle").boundingBox(),
    page.getByTestId("next-frame").boundingBox(),
  ]);
  expect(dockBox?.height ?? 999).toBeLessThanOrEqual(72);
  for (const box of [previousBox, playBox, nextBox]) expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect((legendBox?.y ?? 999) + (legendBox?.height ?? 999)).toBeLessThanOrEqual((dockBox?.y ?? 0) - 8);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(844);

  const more = await openMore(page);
  await expect(more).toHaveAttribute("data-scroll-more", "true");
  const body = more.locator(".context-drawer-body");
  await body.evaluate((element) => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event("scroll")); });
  await expect(more).toHaveAttribute("data-scroll-more", "false");
  await expect(more.getByRole("button", { name: /About & sources/ })).toBeVisible();
  assertNoBrowserErrors();
});
