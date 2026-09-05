// The launch notice: one toast when a newer Wrenote exists, none otherwise.
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateNotice } from "@/components/UpdateNotice";
import { I18nProvider } from "@/i18n/provider";
import type { UpdateStatus } from "@/lib/update";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { dismiss: vi.fn() }) }));
vi.mock("@/lib/update", async (orig) => ({
  ...(await orig<typeof import("@/lib/update")>()),
  getUpdateStatus: vi.fn(),
  openExternal: vi.fn(),
}));

const { toast } = await import("sonner");
const update = await import("@/lib/update");

const status = (over: Partial<UpdateStatus>): UpdateStatus => ({
  current: "0.1.0",
  enabled: true,
  platform: "windows-x86_64",
  index_url: "",
  checked_at: "2026-09-05T08:00:00+00:00",
  latest: "0.1.0",
  available: false,
  download_url: null,
  release_url: null,
  notes: "",
  published_at: null,
  error: null,
  ...over,
});

const show = () =>
  render(
    <I18nProvider>
      <UpdateNotice />
    </I18nProvider>,
  );

describe("UpdateNotice", () => {
  beforeEach(() => vi.mocked(toast).mockClear());

  it("raises a toast with a Download action when a newer version exists", async () => {
    vi.mocked(update.getUpdateStatus).mockResolvedValue(
      status({ latest: "0.2.0", available: true, download_url: "https://x/setup.exe" }),
    );
    show();
    await waitFor(() => expect(toast).toHaveBeenCalled());
    const [title, opts] = vi.mocked(toast).mock.calls[0] as [string, { action?: { label: string; onClick: () => void } }];
    expect(title).toBe("Wrenote 0.2.0 is available");
    expect(opts.action?.label).toBe("Download");
    opts.action?.onClick();
    expect(update.openExternal).toHaveBeenCalledWith("https://x/setup.exe");
  });

  it("stays quiet when up to date, and when the check failed", async () => {
    vi.mocked(update.getUpdateStatus).mockResolvedValue(status({}));
    show();
    vi.mocked(update.getUpdateStatus).mockResolvedValue(status({ error: "unreachable" }));
    show();
    await new Promise((r) => setTimeout(r, 0));
    expect(toast).not.toHaveBeenCalled();
  });
});
