import { createFileRoute } from "@tanstack/react-router";
import DeskApp from "@/desk/App";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return (
    <div className="h-dvh overflow-hidden">
      <DeskApp />
    </div>
  );
}
