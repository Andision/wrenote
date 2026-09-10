// GET /v1/about: what Wrenote is and what it is built on.
//
// The engine reads its Python dependencies' licences from the installed
// distributions at request time, so this can't go stale; everything else
// comes from `wrenote/credits.yaml` and the model catalogue, each entry
// carrying the upstream page where the terms actually live.
import { API_BASE as BASE } from "./api";

export interface Component {
  name: string;
  license?: string;
  version?: string;
  url?: string;
  note?: string;
  kind?: string;
}

export interface About {
  version: string;
  license: string;
  source: string;
  native: Component[];
  web: Component[];
  python: Component[];
  models: Component[];
}

export async function getAbout(): Promise<About> {
  const res = await fetch(`${BASE}/about`);
  if (!res.ok) throw new Error(`about failed (${res.status})`);
  return (await res.json()) as About;
}
