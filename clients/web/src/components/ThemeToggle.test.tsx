// The toggle used to render an empty placeholder for one frame while it
// waited on a `mounted` flag set from an effect. next-themes seeds the theme
// from localStorage in a state initialiser, so the right icon is there on the
// very first render of a client-only app — these hold that.
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/ThemeToggle";
import { I18nProvider } from "@/i18n/provider";

const show = () =>
  render(
    <I18nProvider>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <ThemeToggle />
      </ThemeProvider>
    </I18nProvider>,
  );

const title = () => screen.getByRole("button").getAttribute("title");

describe("ThemeToggle", () => {
  beforeEach(() => localStorage.clear());

  it("shows the stored theme on the first render, with no blank frame", () => {
    localStorage.setItem("theme", "dark");
    const { container } = show();
    expect(container.querySelector("button")).not.toBeNull();
    expect(title()).toBe("Theme: Dark — switch to System");
  });

  it("cycles light → dark → system", () => {
    localStorage.setItem("theme", "light");
    show();
    expect(title()).toBe("Theme: Light — switch to Dark");

    fireEvent.click(screen.getByRole("button"));
    expect(title()).toBe("Theme: Dark — switch to System");

    fireEvent.click(screen.getByRole("button"));
    expect(title()).toBe("Theme: System — switch to Light");
  });
});
