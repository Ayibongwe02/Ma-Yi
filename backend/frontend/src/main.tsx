import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import DeskApp from "./desk/App";
import "./styles.css";

const el = document.getElementById("root");
if (el) {
  createRoot(el).render(
    <StrictMode>
      <div className="h-dvh overflow-hidden">
        <DeskApp />
      </div>
    </StrictMode>
  );
}
