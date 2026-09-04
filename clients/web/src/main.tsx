import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";

import App from "./App.tsx";
import { Overlay } from "./components/Overlay.tsx";
import { I18nProvider } from "./i18n/provider";
import { installDesktopBridge } from "./lib/desktop.ts";
import { isOverlayWindow } from "./lib/overlayBridge.ts";
import "./index.css";

// Under the Tauri shell, expose the same window.wrenoteDesktop the Electron
// preload provides, so components stay shell-agnostic.
installDesktopBridge();

// #overlay → the floating subtitle window (hosted by the desktop shell, or a
// plain browser tab during dev). It skips the theme provider: the overlay is
// always dark-on-translucent regardless of the app theme.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider>
      {isOverlayWindow() ? (
        <Overlay />
      ) : (
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <App />
        </ThemeProvider>
      )}
    </I18nProvider>
  </StrictMode>,
);
