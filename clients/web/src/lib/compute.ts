import { API_BASE as BASE } from "./api";
// Compute runtime: which accelerator build (CPU / Metal / CUDA / Vulkan) the
// engine's inference backends run on. The engine ships one built-in runtime
// and can install accelerated "packs" on demand; installs are jobs (progress
// over the shared /jobs SSE — see lib/jobs.ts). Changing the accelerator
// applies immediately while no backend has been loaded (first-run setup);
// after that the response asks for a restart.

export type Variant = "cpu" | "metal" | "cuda" | "vulkan";
export type Accelerator = "auto" | Variant;

export interface GpuInfo {
  vendor: string;
  name: string;
  vram_mb: number | null;
  unified_memory: boolean;
  driver_version: string | null;
}

/**
 * Why a variant is (or isn't) usable here. A code plus facts, not a sentence:
 * `lib/computeText.ts` turns it into words in the user's language. `detail` is
 * the engine's English rendering, kept as a fallback.
 */
export interface AcceleratorNote {
  variant: string;
  usable: boolean;
  code: string;
  params: Record<string, string>;
  detail: string;
}

/** One runtime a person can choose, with the reasoning to render beside it. */
export interface RuntimeOption {
  variant: Variant;
  usable: boolean;
  installed: boolean;
  builtin: boolean;
  recommended: boolean;
  accelerated: boolean;
  note_code: string; // "download" | "builtin" | "installed" | …
  note: string; // the engine's English rendering of note_code, as a fallback
  download_mb: number | null;
  hardware: AcceleratorNote | null; // the hardware verdict for this variant
}

export interface HardwareInfo {
  os: string;
  arch: string;
  cpu_count: number;
  ram_mb: number | null;
  gpus: GpuInfo[];
  npu: string | null;
  accelerators: string[];
  notes: AcceleratorNote[];
}

export interface PackRelease {
  variant: string;
  version: string;
  size: number;
  url: string;
}

export interface PackInfo {
  variant: Variant;
  builtin: boolean;
  installed: boolean;
  version: string | null;
  available?: boolean;
  release?: PackRelease | null;
}

export interface ComputeStatus {
  platform_tag: string;
  python: string;
  hardware: HardwareInfo;
  builtin: Variant;
  candidates: string[];
  selection: { variant: string; reason: string; chain: string[]; skipped: string[] };
  active: Variant | null;
  packs: PackInfo[];
  options: RuntimeOption[];
  can_switch_without_restart: boolean;
  bad: Record<string, string>;
  vram_budget_mb: number | null;
  config: { accelerator: string; gpu_layers: number | null; vram_budget_mb: number | null };
  index: { url: string; checked: boolean; reachable?: boolean; error?: string | null };
}

async function check(res: Response, what: string): Promise<Response> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`${what} failed (${res.status}): ${detail}`);
  }
  return res;
}

export async function getComputeStatus(): Promise<ComputeStatus> {
  const res = await check(await fetch(`${BASE}/compute/status`), "compute status");
  return (await res.json()) as ComputeStatus;
}

export async function installRuntime(
  variant: Variant,
): Promise<{ job_id: string | null; installed: boolean; can_apply_without_restart?: boolean }> {
  const res = await check(
    await fetch(`${BASE}/compute/install`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ variant }),
    }),
    "runtime install",
  );
  return (await res.json()) as {
    job_id: string | null;
    installed: boolean;
    can_apply_without_restart?: boolean;
  };
}

export async function selectAccelerator(
  accelerator: Accelerator,
): Promise<{ accelerator: string; restart_required: boolean }> {
  const res = await check(
    await fetch(`${BASE}/compute/select`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ accelerator }),
    }),
    "accelerator select",
  );
  return (await res.json()) as { accelerator: string; restart_required: boolean };
}

export async function removeRuntime(
  variant: Variant,
): Promise<{ removed: boolean; restart_required: boolean }> {
  const res = await check(
    await fetch(`${BASE}/compute/packs/${encodeURIComponent(variant)}`, { method: "DELETE" }),
    "runtime remove",
  );
  return (await res.json()) as { removed: boolean; restart_required: boolean };
}

export const VARIANT_LABEL: Record<Variant, string> = {
  cpu: "CPU",
  metal: "Metal (Apple GPU)",
  cuda: "CUDA (NVIDIA)",
  vulkan: "Vulkan (any GPU)",
};

/** The option the engine recommends for this machine, if any is usable. */
export function recommendedOption(status: ComputeStatus): RuntimeOption | undefined {
  return status.options.find((o) => o.recommended);
}

export function formatMb(mb: number | null | undefined): string {
  if (mb == null) return "";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;
}
