import { Outlet } from "react-router";

export default function PublicLayout() {
  return (
    <div className="min-h-screen bg-neutral-50 flex justify-center items-start py-[12vh] px-4">
      <Outlet />
    </div>
  );
}
