import { Link } from "react-router";
import { useStores } from "../stores";

export function Topbar() {
  const { session } = useStores();
  const w = session.workspaces.find((x) => x.slug === session.currentWsSlug);
  return (
    <header className="h-12 bg-white border-b border-neutral-200 flex items-center gap-2.5 px-3.5">
      <Link to={`/${session.currentWsSlug ?? ""}`} className="flex items-center gap-2 h-9 px-2 rounded-md hover:bg-neutral-50">
        <span className="w-5 h-5 rounded-md bg-brand-500 text-white text-xs font-semibold flex items-center justify-center">
          {w?.name.slice(0, 1) ?? "?"}
        </span>
        <span className="text-sm font-medium max-w-[160px] truncate">{w?.name ?? "—"}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="m6 9 6 6 6-6"/></svg>
      </Link>
      <div className="ml-auto flex items-center gap-2">
        <button className="h-7 px-2 border border-neutral-200 rounded-md text-xs text-neutral-500 bg-white">⌘K</button>
        <span className="w-7 h-7 rounded-full bg-brand-500 text-white text-xs font-semibold flex items-center justify-center">
          {session.user?.display_name.slice(0, 1) ?? "?"}
        </span>
      </div>
    </header>
  );
}
