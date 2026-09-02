// Promise-based in-app confirm dialog — replaces window.confirm so we can
// render a styled, backdrop-blurred modal instead of the native browser
// prompt. Usage:
//   if (await confirmDialog({ title: "…", description: "…" })) { … }
import { create } from "zustand";

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as a destructive (red) action. */
  destructive?: boolean;
}

interface ConfirmState {
  open: boolean;
  options: ConfirmOptions | null;
  _resolve: ((ok: boolean) => void) | null;
  request: (options: ConfirmOptions) => Promise<boolean>;
  respond: (ok: boolean) => void;
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  open: false,
  options: null,
  _resolve: null,
  request: (options) =>
    new Promise<boolean>((resolve) => {
      // If a prompt is somehow already open, cancel it before opening a new one.
      get()._resolve?.(false);
      set({ open: true, options, _resolve: resolve });
    }),
  respond: (ok) => {
    get()._resolve?.(ok);
    set({ open: false, options: null, _resolve: null });
  },
}));

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return useConfirmStore.getState().request(options);
}
