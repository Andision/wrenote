import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";

import App from "./App.tsx";
import { Overlay } from "./components/Overlay.tsx";
import { isOverlayWindow } from "./lib/overlayBridge.ts";
import "./index.css";

// #overlay → the floating subtitle window (hosted by the Electron shell, or a
// plain browser tab during dev). It skips the theme provider: the overlay is
// always dark-on-translucent regardless of the app theme.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isOverlayWindow() ? (
      <Overlay />
    ) : (
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <App />
      </ThemeProvider>
    )}
  </StrictMode>,
);
