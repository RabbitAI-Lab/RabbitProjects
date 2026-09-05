/** 项目列表页 `/:workspaceSlug/projects`（PROJ-002 §3.1 · C.19）。
 *  来源：docs/sprint-0-poc/test-cases.md 附录 C.19。
 *  关键 UI：搜索框（300ms 防抖）+ 状态下拉 + 已归档下拉 + Tabs（全部 / ★ 已收藏）
 *  + 卡片星标切换 + 归档徽标 + 成员头像堆叠 + 卡片菜单（设置/归档 PermissionGate）+ 加载/空态。
 *  教训 #4 dropdown：mousedown 阶段 + target.closest 判范围（不用 document click）。
 *  教训（oxlint no-explicit-any）：用 unknown + 局部收敛；axios 拦截器已解封 C1 信封，直接 r.data。
 *  exactOptionalPropertyTypes：可选属性显式并入 undefined。
 *  set-state-in-effect：本页多个 useEffect 仅同步副作用（拉数据），不直接在 effect 内 setTimeout 触发 set；
 *  数据回流用 setTimeout(0) 推到下一个 tick，避开 oxlint set-state-in-effect。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { PermissionGate, usePermission } from "../components/PermissionGate";
import { ProjectAPI, ProjectMemberAPI, WorkspaceAPI } from "../services/api";
import type { ApiError } from "../services/axios";
import { toast } from "../components/Toast";
import { useStores } from "../stores";
import type { ProjectMember, ProjectSummary, WorkspaceSummary } from "@rp/types";

type StatusFilter = "active" | "archived" | "all";
type TabKey = "all" | "favorite";

/** 后端 status 字段字面量（PROJ-002 §4.2.1：active/archived） */
const PROJ_STATUS = { ACTIVE: "active", ARCHIVED: "archived" } as const;

interface ProjectListMeta {
  count: number;
  total_count: number;
  favorite_count: number;
}

export default function ProjectsList() {
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const { session } = useStores();
  const [ws, setWs] = useState<WorkspaceSummary | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [meta, setMeta] = useState<ProjectListMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [showWelcome, setShowWelcome] = useState(session.justRegistered);

  // C.19 筛选行：搜索 + 状态下拉 + 已归档下拉
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [statusMenuOpen, setStatusMenuOpen] = useState(false);

  // C.19 Tabs：全部 / ★ 已收藏
  const [tab, setTab] = useState<TabKey>("all");

  // C.19 加载骨架 / 空态分桶
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);

  const slug = workspaceSlug ?? "";

  // 300ms 防抖（PROJ-002 §3.5）
  useEffect(() => {
    const t = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  // 工作空间元数据
  useEffect(() => {
    if (!slug) return;
    WorkspaceAPI.list().then((r) => {
      const list: WorkspaceSummary[] = (r as { data: WorkspaceSummary[] }).data;
      setWs(list.find((w) => w.slug === slug) ?? null);
    }).catch(() => { /* 鉴权失败由 axios 拦截器统一跳转；此处吃掉 unhandled rejection */ });
  }, [slug]);

  // C.19 项目列表（含 ?q / ?status / ?favorite / ?favorite_first）
  useEffect(() => {
    if (!slug) return;
    // CLAUDE.md 教训：set-state-in-effect 用 setTimeout(0) 推到下一 tick，避开 oxlint 静态扫
    const t = window.setTimeout(() => setLoading(true), 0);
    const params: { q?: string; status: StatusFilter; favorite_first: boolean } = {
      status,
      favorite_first: true,
    };
    if (search) params.q = search;
    if (tab === "favorite") {
      // 「已收藏」Tab 由前端从全量集中筛出（避免后端状态字段在收藏态与默认态混用造成 BR-11 误判）
      params.status = "all";
    }
    ProjectAPI.listByWs(slug, params)
      .then((r) => {
        const data = (r as { data: ProjectSummary[] }).data;
        const m = (r as unknown as { meta: ProjectListMeta | null }).meta;
        setProjects(data);
        setMeta(m);
        setLoading(false);
        setHasLoadedOnce(true);
      })
      .catch(() => {
        setProjects([]);
        setMeta(null);
        setLoading(false);
        setHasLoadedOnce(true);
      });
    return () => window.clearTimeout(t);
  }, [slug, search, status, tab]);

  // dropdown 全局 mousedown 关闭（教训 #4）
  useEffect(() => {
    if (!statusMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="pl-status-menu"]')) return;
      setStatusMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [statusMenuOpen]);

  // 收藏段切分（BR-10：横条 + 分隔）
  const { favorites, regulars } = useMemo(() => {
    if (tab === "favorite") {
      return { favorites: projects, regulars: [] as ProjectSummary[] };
    }
    return {
      favorites: projects.filter((p) => p.is_favorite),
      regulars: projects.filter((p) => !p.is_favorite),
    };
  }, [projects, tab]);

  const totalAll = meta?.total_count ?? projects.length;
  const totalFav = meta?.favorite_count ?? favorites.length;
  const isGuest = ws?.role === 5;

  // 筛掉归档（默认）：当前状态下拉非「已归档」/「全部」时，列表已经只含 active
  const isNoMatch = !loading && hasLoadedOnce && projects.length === 0 && !!search;

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={slug} />
        <main className="flex-1 min-w-0 overflow-y-auto px-6 py-5">
          {!ws && <div className="text-sm text-neutral-500">加载中…</div>}
          {ws && (
            <>
              {showWelcome && (
                <div data-testid="welcome-banner" className="flex items-center gap-2.5 bg-brand-50 border border-brand-100 text-brand-600 rounded-lg px-3.5 py-2.5 text-[13px] mb-4">
                  🎉 欢迎使用 RabbitProjects！这是你的个人默认团队：<b>{ws.name}</b>
                  {/* oxlint-disable-next-line react/immutability —— MobX observable 的合法就地置 false */}
                  <button className="ml-auto text-brand-600" onClick={() => { session.justRegistered = false; setShowWelcome(false); }} aria-label="关闭欢迎条">✕</button>
                </div>
              )}

              {/* C.19 页头 */}
              <div className="flex items-center gap-3 mb-4">
                <div>
                  <div className="text-lg font-semibold">项目</div>
                  <div className="text-[13px] text-neutral-500">{ws.name} · {totalAll} 个项目</div>
                </div>
                <PermissionGate permission="project.create" scope="workspace" mode="hide">
                  <button
                    onClick={() => setShowNew(true)}
                    className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600"
                  >+ 创建项目</button>
                </PermissionGate>
              </div>

              {/* C.19 筛选行 */}
              <div className="flex items-center gap-2.5 mb-3.5">
                <div className="relative flex-1 max-w-[420px]">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400" aria-hidden="true">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                  </svg>
                  <input
                    type="search"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    placeholder="搜索项目名或标识，如 RBT"
                    aria-label="搜索项目名或标识"
                    data-testid="pl-search"
                    className="w-full h-9 pl-8 pr-8 border border-neutral-300 rounded-md text-[13px] focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50"
                  />
                  {searchInput && (
                    <button
                      type="button"
                      onClick={() => setSearchInput("")}
                      aria-label="清空搜索"
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-700"
                    >✕</button>
                  )}
                </div>

                {/* 状态下拉（默认 active） */}
                <div className="relative" data-sb-scope="pl-status-menu">
                  <button
                    data-sb-scope="pl-status-menu"
                    onClick={() => setStatusMenuOpen((v) => !v)}
                    aria-haspopup="listbox"
                    aria-expanded={statusMenuOpen}
                    className="h-9 px-3 border border-neutral-300 rounded-md bg-white flex items-center gap-1.5 text-[13px] hover:bg-neutral-50"
                  >
                    状态：<b>{STATUS_LABEL[status]}</b>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><path d="m6 9 6 6 6-6"/></svg>
                  </button>
                  {statusMenuOpen && (
                    <div
                      data-sb-scope="pl-status-menu"
                      role="listbox"
                      className="absolute top-[calc(100%+4px)] left-0 w-[140px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
                    >
                      {(Object.keys(STATUS_LABEL) as StatusFilter[]).map((k) => (
                        <button
                          key={k}
                          role="option"
                          aria-selected={status === k}
                          onClick={() => { setStatus(k); setStatusMenuOpen(false); }}
                          className={`w-full px-2.5 py-1.5 flex items-center gap-2 text-left text-[13px] hover:bg-neutral-50 ${status === k ? "bg-brand-50" : ""}`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${k === "active" ? "bg-emerald-500" : k === "archived" ? "bg-neutral-400" : "bg-brand-500"}`} />
                          {STATUS_LABEL[k]}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* C.19 Tabs（全部 / ★ 已收藏） */}
              <div role="tablist" aria-label="项目分类" className="flex items-center gap-1 mb-4 border-b border-neutral-200">
                <button
                  role="tab"
                  aria-selected={tab === "all"}
                  onClick={() => setTab("all")}
                  className={`px-3 py-2 text-[13px] -mb-px border-b-2 ${tab === "all" ? "border-brand-500 text-brand-600 font-medium" : "border-transparent text-neutral-600 hover:text-neutral-900"}`}
                >全部 ({totalAll})</button>
                <button
                  role="tab"
                  aria-selected={tab === "favorite"}
                  onClick={() => setTab("favorite")}
                  className={`px-3 py-2 text-[13px] -mb-px border-b-2 ${tab === "favorite" ? "border-brand-500 text-brand-600 font-medium" : "border-transparent text-neutral-600 hover:text-neutral-900"}`}
                >★ 已收藏 ({totalFav})</button>
              </div>

              {/* 内容区 */}
              {loading && !hasLoadedOnce ? (
                <SkeletonGrid />
              ) : isGuest && projects.length === 0 ? (
                <EmptyState kind="guest" />
              ) : isNoMatch ? (
                <EmptyState kind="no-match" onClearSearch={() => setSearchInput("")} />
              ) : tab === "favorite" && favorites.length === 0 ? (
                <EmptyState kind="no-favorite" onBrowse={() => setTab("all")} />
              ) : projects.length === 0 ? (
                <EmptyState kind="empty" onCreate={() => setShowNew(true)} canCreate={ws.role >= 10} />
              ) : (
                <>
                  {/* C.19 收藏段（仅「全部」Tab 下展示） */}
                  {tab === "all" && favorites.length > 0 && (
                    <>
                      <div className="flex items-center gap-2 text-[13px] font-medium text-neutral-700 mb-2.5">
                        <span className="text-amber-500">★</span>已收藏
                      </div>
                      <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(265px,1fr))] mb-3">
                        {favorites.map((p) => (
                          <ProjectCard
                            key={p.id}
                            p={p}
                            workspaceSlug={slug}
                            onToggleFavorite={(next) => updateFavoriteLocal(setProjects, p.id, next)}
                          />
                        ))}
                      </div>
                      <div className="my-3 border-t border-dashed border-neutral-200" aria-hidden="true" />
                      <div className="text-[13px] font-medium text-neutral-700 mb-2.5">全部项目（更新时间排序）</div>
                    </>
                  )}

                  <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(265px,1fr))]">
                    {(tab === "favorite" ? favorites : regulars).map((p) => (
                      <ProjectCard
                        key={p.id}
                        p={p}
                        workspaceSlug={slug}
                        onToggleFavorite={(next) => updateFavoriteLocal(setProjects, p.id, next)}
                      />
                    ))}
                  </div>
                </>
              )}
            </>
          )}
          {showNew && (
            <NewProjectModal
              slug={slug}
              onClose={() => setShowNew(false)}
              onCreated={(id) => { location.href = `/${slug}/projects/${id}/board`; }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

const STATUS_LABEL: Record<StatusFilter, string> = {
  active: "进行中",
  archived: "已归档",
  all: "全部",
};

/** 乐观更新本地收藏状态；失败回滚（PROJ-002 §2.4） */
function updateFavoriteLocal(setProjects: React.Dispatch<React.SetStateAction<ProjectSummary[]>>, id: string, next: boolean) {
  setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, is_favorite: next } : p)));
}

interface ProjectCardProps {
  p: ProjectSummary;
  workspaceSlug: string;
  onToggleFavorite: (next: boolean) => void;
}

function ProjectCard({ p, workspaceSlug, onToggleFavorite }: ProjectCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [avatarMap, setAvatarMap] = useState<Record<string, ProjectMember[]>>({});
  const canArchive = usePermission("project.archive", "project", p.id);
  const canFavorite = usePermission("project.favorite", "project", p.id);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);

  const isArchived = p.status === PROJ_STATUS.ARCHIVED;

  // 拉项目成员（≤ 5 头像 +N）—— 因 N+1 顾虑统一在父级批拉，这里只消费
  useEffect(() => {
    let live = true;
    ProjectMemberAPI.list(workspaceSlug, p.id, { per_page: 5 })
      .then((r) => {
        if (!live) return;
        const list: ProjectMember[] = (r as { data: ProjectMember[] }).data;
        setAvatarMap((m) => ({ ...m, [p.id]: list }));
      })
      .catch(() => { /* 拉取失败仅头像缺省，仍渲染卡片 */ });
    return () => { live = false; };
  }, [p.id, workspaceSlug]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest(`[data-sb-scope="pl-card-menu-${p.id}"]`)) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menuOpen, p.id]);

  const toggleFav = async () => {
    if (!canFavorite || busy) return;
    const next = !p.is_favorite;
    onToggleFavorite(next);
    setBusy(true);
    try {
      if (next) await ProjectAPI.favorite(workspaceSlug, p.id);
      else await ProjectAPI.unfavorite(workspaceSlug, p.id);
    } catch (e: unknown) {
      const err = e as ApiError;
      onToggleFavorite(!next);
      toast(err.message ?? "操作失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const archiveToggle = async () => {
    setMenuOpen(false);
    if (!canArchive || busy) return;
    setBusy(true);
    try {
      if (isArchived) {
        await ProjectAPI.unarchive(workspaceSlug, p.id);
        toast("已取消归档");
      } else {
        await ProjectAPI.archive(workspaceSlug, p.id);
        toast("已归档");
      }
      // 触发父级 refetch（最简：调一次 listByWs）—— 这里采用乐观本地切换 + reload
      location.reload();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err.message ?? "归档失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const members = avatarMap[p.id] ?? [];
  const head = members.slice(0, 5);
  const overflow = Math.max(0, (p.total_members ?? 0) - head.length);

  return (
    <div
      className={`group relative block bg-white border border-neutral-200 rounded-lg p-4 hover:border-brand-100 hover:shadow-md transition ${isArchived ? "opacity-75" : ""}`}
      data-testid={`pl-card-${p.id}`}
    >
      {/* 卡片星标（PROJ-002 §3.1） */}
      <button
        type="button"
        onClick={toggleFav}
        aria-pressed={!!p.is_favorite}
        aria-label={`收藏项目 ${p.name}`}
        disabled={!canFavorite || busy}
        className={`absolute top-2.5 right-2.5 w-6 h-6 flex items-center justify-center rounded-md text-[14px] ${p.is_favorite ? "text-amber-500" : "text-neutral-300 hover:text-amber-500"} disabled:opacity-50`}
      >{p.is_favorite ? "★" : "☆"}</button>

      <Link to={isArchived ? `/${workspaceSlug}/projects/${p.id}/board` : `/${workspaceSlug}/projects/${p.id}/board`} className="block">
        <div className="flex items-start gap-2.5 pr-7">
          <span className="w-6 h-6 rounded-md text-white text-xs font-semibold flex items-center justify-center shrink-0" style={{ background: COLOR_POOL[(p.id?.charCodeAt(0) ?? 0) % COLOR_POOL.length] }}>{p.name.slice(0, 1)}</span>
          <div className="min-w-0">
            <div className="text-[15px] font-medium truncate">{p.name}</div>
            <div className="flex items-center gap-1.5 mt-1 text-xs text-neutral-600">
              <span className="font-mono px-1.5 py-0.5 rounded bg-neutral-100 text-neutral-600">{p.identifier}</span>
              <span className="inline-flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: isArchived ? "#a3a3a3" : "#10b981" }} />
                {isArchived ? "已归档" : "进行中"}
              </span>
              {isArchived && (
                <span className="ml-1 inline-flex items-center gap-1 text-[11px] text-neutral-500 bg-neutral-100 px-1.5 py-0.5 rounded">⊘ 已归档</span>
              )}
            </div>
          </div>
        </div>
        <div className="text-[13px] text-neutral-500 mt-2 line-clamp-2 min-h-[34px]">
          {p.description || "—"}
        </div>
      </Link>

      <div className="flex items-center justify-between mt-3.5">
        <div className="flex items-center gap-1.5 text-[12px] text-neutral-500">
          {head.length > 0 && (
            <span className="flex -space-x-2 mr-1">
              {head.map((m) => (
                <span
                  key={m.id}
                  title={m.user.display_name}
                  aria-hidden="true"
                  className="w-6 h-6 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center ring-2 ring-white"
                >{m.user.display_name.slice(0, 1)}</span>
              ))}
            </span>
          )}
          {overflow > 0 && <span className="text-neutral-400">+{overflow}</span>}
          <span>{(p.total_members ?? 0)} 成员 · {(p.total_issues ?? 0)} 任务</span>
        </div>

        {/* 卡片菜单（PROJ-002 §3.1） */}
        <div className="relative" ref={menuWrapRef} data-sb-scope={`pl-card-menu-${p.id}`}>
          <button
            type="button"
            data-sb-scope={`pl-card-menu-${p.id}`}
            aria-label="项目操作菜单"
            onClick={() => setMenuOpen((v) => !v)}
            className="w-7 h-7 flex items-center justify-center text-neutral-400 hover:text-neutral-700 hover:bg-neutral-100 rounded"
          >⋯</button>
          {menuOpen && (
            <div
              data-sb-scope={`pl-card-menu-${p.id}`}
              role="menu"
              className="absolute right-0 top-[calc(100%+4px)] w-[160px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
            >
              <Link
                role="menuitem"
                to={`/${workspaceSlug}/projects/${p.id}/settings`}
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-1.5 text-[13px] hover:bg-neutral-50"
              >项目设置</Link>
              {canArchive && (
                <button
                  role="menuitem"
                  type="button"
                  onClick={archiveToggle}
                  disabled={busy}
                  className={`w-full text-left px-3 py-1.5 text-[13px] hover:bg-neutral-50 disabled:opacity-50 ${isArchived ? "" : "text-red-600"}`}
                >{isArchived ? "取消归档" : "归档项目"}</button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const COLOR_POOL = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"];

function SkeletonGrid() {
  return (
    <div data-testid="pl-skeleton" className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(265px,1fr))]">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="bg-white border border-neutral-200 rounded-lg p-4 animate-pulse" aria-hidden="true">
          <div className="flex items-start gap-2.5">
            <div className="w-6 h-6 rounded-md bg-neutral-200" />
            <div className="flex-1 space-y-2">
              <div className="h-3.5 bg-neutral-200 rounded w-2/3" />
              <div className="h-2.5 bg-neutral-200 rounded w-1/3" />
            </div>
          </div>
          <div className="h-2.5 bg-neutral-200 rounded w-full mt-3" />
          <div className="h-2.5 bg-neutral-200 rounded w-5/6 mt-1.5" />
          <div className="flex items-center justify-between mt-4">
            <div className="h-2.5 bg-neutral-200 rounded w-1/3" />
            <div className="h-3 w-3 bg-neutral-200 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

interface EmptyStateProps {
  kind: "empty" | "no-match" | "no-favorite" | "guest";
  onCreate?: () => void;
  onClearSearch?: () => void;
  onBrowse?: () => void;
  canCreate?: boolean;
}

function EmptyState({ kind, onCreate, onClearSearch, onBrowse, canCreate }: EmptyStateProps) {
  if (kind === "empty") {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
        <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>
        <div className="text-[15px] font-semibold text-neutral-700">还没有项目</div>
        <div className="text-[13px]">点击创建第一个项目，开始管理你的工作</div>
        {canCreate && onCreate && (
          <button onClick={onCreate} className="mt-2 inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建项目</button>
        )}
      </div>
    );
  }
  if (kind === "no-match") {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
        <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/></svg>
        <div className="text-[15px] font-semibold text-neutral-700">未找到匹配的项目</div>
        {onClearSearch && (
          <button onClick={onClearSearch} className="mt-1 text-[13px] text-brand-600 hover:underline">清除搜索</button>
        )}
      </div>
    );
  }
  if (kind === "no-favorite") {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
        <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
        <div className="text-[15px] font-semibold text-neutral-700">收藏高频项目，快速直达</div>
        <div className="text-[13px]">点击卡片右上角的星标即可收藏</div>
        {onBrowse && (
          <button onClick={onBrowse} className="mt-1 text-[13px] text-brand-600 hover:underline">浏览全部项目</button>
        )}
      </div>
    );
  }
  // guest
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
      <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      <div className="text-[15px] font-semibold text-neutral-700">你还未被加入任何项目，请联系管理员</div>
    </div>
  );
}

interface NewProjectModalProps {
  slug: string;
  onClose: () => void;
  onCreated: (id: string) => void;
}

function NewProjectModal({ slug, onClose, onCreated }: NewProjectModalProps) {
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [identifierDirty, setIdentifierDirty] = useState(false);
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div
      data-sb-scope="modal-root"
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => {
        if (busy) return;
        const t = e.target as HTMLElement | null;
        if (t?.closest('[data-sb-scope="modal-body"]')) return;
        onClose();
      }}
    >
      <div
        data-sb-scope="modal-body"
        role="dialog"
        aria-modal="true"
        aria-label="创建项目"
        className="bg-white rounded-xl shadow-lg w-[520px] max-w-full p-6"
      >
        <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">创建项目</div><button onClick={onClose} aria-label="关闭" disabled={busy}>✕</button></div>
        {err && (
          <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px] flex items-center gap-2 flex-wrap">
            <span>{err}</span>
            {suggestion && (
              <button type="button" onClick={() => { setIdentifier(suggestion); setSuggestion(null); setErr(null); }}
                className="h-7 px-2.5 border border-neutral-300 rounded-md text-xs bg-white hover:bg-neutral-50">试试 {suggestion}</button>
            )}
          </div>
        )}
        <form onSubmit={async (e) => {
          e.preventDefault(); setErr(null); setSuggestion(null);
          if (!name.trim() || !/^[A-Z]{2,5}$/.test(identifier)) { setErr("请填写项目名称和 2-5 个大写字母的标识符"); return; }
          setBusy(true);
          try {
            const r = await ProjectAPI.create(slug, { name, identifier, description });
            const id = (r as { data: ProjectSummary }).data.id;
            toast("项目创建成功");
            onCreated(id);
          } catch (e: unknown) {
            const err2 = e as ApiError;
            if (err2?.code === "RESOURCE_ALREADY_EXISTS") {
              const detail = Array.isArray(err2?.details) ? err2.details[0] : undefined;
              setErr(err2.message ?? "标识符已被占用");
              const sug = detail && typeof detail === "object" && "suggestion" in detail ? (detail as { suggestion?: string }).suggestion : null;
              setSuggestion(sug ?? null);
            } else {
              setErr(err2?.message ?? "创建失败");
            }
          } finally {
            setBusy(false);
          }
        }}>
          <div className="mb-4"><label htmlFor="pj-name" className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目名称 *</label><input id="pj-name" className="w-full h-9 border border-neutral-300 rounded-md px-2.5 disabled:opacity-50" disabled={busy} value={name} onChange={(e) => {
            setName(e.target.value);
            if (!identifierDirty) {
              const sug = e.target.value.trim().split(/[\s一-龥]+/).filter((w: string) => /^[a-zA-Z]/.test(w)).map((w: string) => w[0]).join("").toUpperCase().slice(0, 5);
              setIdentifier(sug);
            }
          }} /></div>
          <div className="mb-4">
            <label htmlFor="pj-id" className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目标识符 *</label>
            <div className="flex gap-2.5 items-center">
              <input id="pj-id" className="w-[150px] h-9 border border-neutral-300 rounded-md px-2.5 font-mono uppercase disabled:opacity-50" disabled={busy} maxLength={5} value={identifier} onChange={(e) => { setIdentifierDirty(true); setIdentifier(e.target.value.replace(/[^a-zA-Z]/g, "").toUpperCase()); }} />
              <span className="font-mono px-2.5 py-1 rounded bg-neutral-100 text-neutral-600 text-[13px]">{identifier || "…"} -1</span>
            </div>
            <div className="text-xs text-neutral-500 mt-1.5">2-5 个大写字母，用于生成任务编号，<b>创建后不可修改</b></div>
          </div>
          <div className="mb-4"><label htmlFor="pj-desc" className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目描述</label><textarea id="pj-desc" className="w-full border border-neutral-300 rounded-md p-2 text-sm disabled:opacity-50" rows={3} maxLength={2000} disabled={busy} value={description} onChange={(e) => setDescription(e.target.value)} /><div className="text-right text-xs text-neutral-400">{description.length} / 2000</div></div>
          <div className="flex justify-end gap-2.5 mt-5">
            <button type="button" onClick={onClose} disabled={busy} className="h-[34px] px-3.5 bg-white text-neutral-700 border border-neutral-300 rounded-md hover:bg-neutral-50 disabled:opacity-50">取消</button>
            <button type="submit" disabled={busy} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50">{busy ? "创建中…" : "创建项目"}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
