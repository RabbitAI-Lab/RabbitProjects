import { Outlet } from "react-router";

export default function AppLayout() {
  return (
    <div className="min-h-screen bg-neutral-50">
      <Outlet />
    </div>
  );
}
