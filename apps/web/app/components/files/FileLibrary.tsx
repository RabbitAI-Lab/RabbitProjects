import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  FileLibraryAPI,
  ProjectMemberAPI,
  ProjectAPI,
  unwrap,
  type FileFolderRow,
  type LibraryFileRow,
  type StorageUsage,
} from "../../services/api";
import { toast } from "../Toast";
import {
  AttachToTaskModal,
  ConfirmDialog,
  MoveTargetPicker,
  NameInputDialog,
  PopoverMenu,
  QuotaConfirmDialog,
  VisibilityEditor,
  type PopItem,
} from "./FileDialogs";
import {
  TYPE_FILTERS,
  fileIcon,
  fileTypeName,
  humanSize,
  humanSpeed,
  shortDate,
  trashDaysLeft,
  withinWeek,
} from "./filelib-shared";

/** FILE-002 §3.1 项目文件库页（C.112~C.118）——左树右表双视图 + 预签名直传三步。
 *  - 上传链路复用 FILE-001 三步协议（presign → XHR PUT 直传 MinIO → complete），
 *    Django 零字节流（§2.1 时序）；并行多文件（一次 ≤20，§2.6）。
 *  - 可见性剪枝在后端（folder_tree / list_files 单入口 can_view_file）——前端如实渲染；
 *    🔒/👥 角标来自行内 visibility 字段。
 *  - 权限差异演出：VIEWER 上传禁用（file.upload 403 前置）；可见性菜单项仅 ADMIN。 */

interface FilesMeta {
  total_count: number;
  total_size_bytes?: number;
}

interface UploadItem {
  key: string;
  file: File;
  name: string;
  pct: number;
  speed: string;
  state: "uploading" | "done" | "failed";
  statusText: string;
  /** 里程碑播报水位（25/50/75/100，C.115 aria-live）。 */
  announced: number;
}

type MenuFor = { kind: "file"; row: LibraryFileRow } | { kind: "folder"; row: FileFolderRow } | null;

export function FileLibrary({ slug, projectId, canUpload, isAdmin }: {
  slug: string; projectId: string; canUpload: boolean; isAdmin: boolean;
}) {
  const navigate = useNavigate();
  // ── 数据 ──
  const [folders, setFolders] = useState<FileFolderRow[]>([]);
  const [treeLoading, setTreeLoading] = useState(true);
  const [curFolderId, setCurFolderId] = useState<string | null>(null); // null = 根（项目文件）
  const [files, setFiles] = useState<LibraryFileRow[]>([]);
  const [filesMeta, setFilesMeta] = useState<FilesMeta | null>(null);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [members, setMembers] = useState<Array<{ id: string; name: string }>>([]);
  const [storage, setStorage] = useState<StorageUsage | null>(null);
  const [trashCount, setTrashCount] = useState<number | null>(null);

  // ── 工具条（C.112/C.113）──
  const [view, setView] = useState<"list" | "grid">("list");
  const [nameInput, setNameInput] = useState("");
  const [nameFilter, setNameFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [uploaderFilter, setUploaderFilter] = useState("");
  const [timeFilter, setTimeFilter] = useState("");
  const [filterMenu, setFilterMenu] = useState<"type" | "uploader" | "time" | null>(null);

  // ── 交互态 ──
  const [dropOn, setDropOn] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [announce, setAnnounce] = useState("");
  const [menuFor, setMenuFor] = useState<MenuFor>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const [moveFor, setMoveFor] = useState<MenuFor>(null);
  const [attachFor, setAttachFor] = useState<LibraryFileRow | null>(null);
  const [visFor, setVisFor] = useState<MenuFor>(null);
  const [delFor, setDelFor] = useState<MenuFor>(null);
  const [newDirOpen, setNewDirOpen] = useState(false);
  const [renameFolderFor, setRenameFolderFor] = useState<FileFolderRow | null>(null);
  const [quotaConfirm, setQuotaConfirm] = useState<{ used: number; quota: number; incoming: number; batch: File[] } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const xhrsRef = useRef(new Map<string, XMLHttpRequest>());

  const byId = useMemo(() => new Map(folders.map((f) => [f.id, f])), [folders]);
  const childrenOf = useMemo(() => {
    const m = new Map<string | null, FileFolderRow[]>();
    for (const f of folders) {
      const list = m.get(f.parent_id) ?? [];
      list.push(f);
      m.set(f.parent_id, list);
    }
    return m;
  }, [folders]);
  const curFolder = curFolderId ? byId.get(curFolderId) ?? null : null;

  const pathChain = useMemo(() => {
    const segs: FileFolderRow[] = [];
    let cur = curFolder;
    while (cur) { segs.unshift(cur); cur = cur.parent_id ? byId.get(cur.parent_id) ?? null : null; }
    return segs;
  }, [curFolder, byId]);

  /** 目录自身+后代集合（移动禁选，BR-04 前端预判）。 */
  const descendantsOf = useCallback((folderId: string): Set<string> => {
    const out = new Set<string>([folderId]);
    const walk = (pid: string) => {
      for (const c of childrenOf.get(pid) ?? []) { out.add(c.id); walk(c.id); }
    };
    walk(folderId);
    return out;
  }, [childrenOf]);

  const memberName = useCallback((uid: string | null | undefined) => {
    if (!uid) return "—";
    return members.find((m) => m.id === uid)?.name ?? "…";
  }, [members]);

  // ── 取数 ──
  const reloadTree = useCallback(() => {
    FileLibraryAPI.folders(slug, projectId)
      .then((r) => { setFolders(unwrap<FileFolderRow[]>(r) ?? []); })
      .catch(() => setFolders([]))
      .finally(() => setTreeLoading(false));
  }, [slug, projectId]);

  const reloadStorage = useCallback(() => {
    FileLibraryAPI.storage(slug, projectId)
      .then((r) => { setStorage(unwrap<StorageUsage>(r) ?? null); })
      .catch(() => setStorage(null));
  }, [slug, projectId]);

  const reloadTrashCount = useCallback(() => {
    if (!canUpload) return; // file.delete CONTRIBUTOR+；VIEWER 无回收站口径（计数不请求）
    FileLibraryAPI.trash(slug, projectId, { per_page: 1 })
      .then((r) => { setTrashCount(((r as unknown as { meta?: { total_count?: number } }).meta?.total_count) ?? 0); })
      .catch(() => setTrashCount(null));
  }, [slug, projectId, canUpload]);

  const reloadFiles = useCallback(() => {
    if (!curFolderId) { setFiles([]); setFilesMeta(null); return; }
    setLoadingFiles(true);
    FileLibraryAPI.files(slug, projectId, curFolderId, {
      ...(nameFilter ? { name: nameFilter } : {}),
      ...(typeFilter ? { type: typeFilter } : {}),
      ...(uploaderFilter ? { uploaded_by: uploaderFilter } : {}),
      ordering: "-created_at",
      per_page: 100,
      expand: "uploaded_by",
    })
      .then((r) => {
        setFiles(unwrap<LibraryFileRow[]>(r) ?? []);
        setFilesMeta(((r as unknown as { meta?: FilesMeta }).meta) ?? null);
      })
      .catch(() => { setFiles([]); setFilesMeta(null); })
      .finally(() => setLoadingFiles(false));
  }, [slug, projectId, curFolderId, nameFilter, typeFilter, uploaderFilter]);

  useEffect(() => { reloadTree(); reloadStorage(); reloadTrashCount(); }, [reloadTree, reloadStorage, reloadTrashCount]);
  useEffect(() => {
    ProjectMemberAPI.list(slug, projectId, { per_page: 100 })
      .then((r) => {
        const rows = unwrap<Array<{ user: { id: string; display_name: string } }>>(r) ?? [];
        setMembers(rows.map((x) => ({ id: x.user.id, name: x.user.display_name })));
      })
      .catch(() => setMembers([]));
  }, [slug, projectId]);

  // 名称过滤防抖 300ms（C.112/C.113）
  useEffect(() => {
    const t = setTimeout(() => setNameFilter(nameInput.trim()), 300);
    return () => clearTimeout(t);
  }, [nameInput]);
  useEffect(() => {
    const t = setTimeout(() => { reloadFiles(); }, 0); // setState-in-effect 规避（table.tsx 同款）
    return () => clearTimeout(t);
  }, [reloadFiles]);

  /** 时间筛选（最近一周）为客户端过滤（后端无时间参数——偏差登记）。 */
  const visibleRows = useMemo(() => {
    if (timeFilter !== "week") return files;
    return files.filter((f) => withinWeek(f.created_at));
  }, [files, timeFilter]);
  const anyFilter = Boolean(nameFilter || typeFilter || uploaderFilter || timeFilter);

  // ── 上传三步（C.114/C.115：presign → PUT → complete；配额 ≥95% 预检）──
  const patchUpload = (key: string, patch: Partial<UploadItem>) =>
    setUploads((cur) => cur.map((u) => (u.key === key ? { ...u, ...patch } : u)));

  async function uploadOne(f: File, folderId: string) {
    const item: UploadItem = {
      key: crypto.randomUUID(), file: f, name: f.name, pct: 0, speed: "—",
      state: "uploading", statusText: "准备上传…", announced: 0,
    };
    setUploads((cur) => [...cur, item]);
    try {
      const pre = await FileLibraryAPI.presign(slug, projectId, folderId, {
        file_name: f.name, file_size: f.size, content_type: f.type || "application/octet-stream",
      });
      const p = unwrap<{ asset_id: string; upload_url: string; fields: Record<string, string> }>(pre);
      patchUpload(item.key, { statusText: "直传 MinIO（Django 零字节流）" });
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhrsRef.current.set(item.key, xhr);
        xhr.open("PUT", p.upload_url);
        xhr.setRequestHeader("Content-Type", f.type || p.fields["Content-Type"] || "application/octet-stream");
        let lastT = performance.now();
        let lastLoaded = 0;
        xhr.upload.onprogress = (e) => {
          if (!e.lengthComputable) return;
          const pct = Math.round((e.loaded / e.total) * 100);
          const now = performance.now();
          const speed = e.loaded > lastLoaded && now > lastT ? ((e.loaded - lastLoaded) / ((now - lastT) / 1000)) : 0;
          lastT = now; lastLoaded = e.loaded;
          const milestone = Math.min(100, Math.floor(pct / 25) * 25);
          patchUpload(item.key, { pct, speed: humanSpeed(speed) });
          if (milestone > item.announced && milestone > 0) {
            item.announced = milestone; // C.115：里程碑播报（25/50/75/100%）
            setAnnounce(`${f.name} ${milestone}%`);
          }
        };
        xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`直传失败：HTTP ${xhr.status}`)));
        xhr.onerror = () => reject(new Error("直传失败：网络错误"));
        xhr.onabort = () => reject(new Error("已取消"));
        xhr.send(f);
      });
      await FileLibraryAPI.complete(slug, projectId, p.asset_id);
      patchUpload(item.key, { state: "done", pct: 100, speed: "完成", statusText: "完成，已入列表" });
      setAnnounce(`${f.name} 100%`);
      reloadFiles(); reloadTree(); reloadStorage(); // 完成即插入列表（C.115）
    } catch (e: unknown) {
      xhrsRef.current.delete(item.key);
      const msg = e instanceof Error ? e.message : "上传失败";
      patchUpload(item.key, { state: "failed", statusText: msg });
    } finally {
      xhrsRef.current.delete(item.key);
    }
  }

  function startUpload(list: File[]) {
    if (!canUpload) { toast("访客只读：file.upload 403（PERM_ROLE_INSUFFICIENT）", "warning"); return; }
    if (!curFolderId) { toast("请先在左侧选择目录（文件需存放于目录中）", "warning"); return; }
    const batch = list.slice(0, 20); // §2.6：一次 ≤20，超出分批提示
    if (list.length > 20) toast(`一次最多上传 20 个文件，本次上传前 20 个`, "info");
    const incoming = batch.reduce((s, f) => s + f.size, 0);
    void FileLibraryAPI.storage(slug, projectId)
      .then((r) => {
        const st = unwrap<StorageUsage>(r);
        // 配额预检（C.115 / BR-11：≥95% 弹层「仍要上传」/「取消」）
        if (st && st.quota_bytes > 0 && (st.used_bytes + incoming) / st.quota_bytes >= 0.95) {
          setQuotaConfirm({ used: st.used_bytes, quota: st.quota_bytes, incoming, batch });
          return;
        }
        batch.forEach((f) => { void uploadOne(f, curFolderId); }); // 多文件并行
      })
      .catch(() => { batch.forEach((f) => { void uploadOne(f, curFolderId); }); }); // 预检失败不阻断（presign 侧 409 兜底）
  }

  function retryUpload(item: UploadItem) {
    if (!curFolderId) return;
    patchUpload(item.key, { state: "uploading", pct: 0, announced: 0, statusText: "已重申 presign，续传中" });
    void uploadOne(item.file, curFolderId);
  }

  function cancelUpload(key: string) {
    xhrsRef.current.get(key)?.abort();
    setUploads((cur) => cur.filter((u) => u.key !== key));
  }

  // ── 文件操作（C.116）──
  async function downloadFile(f: LibraryFileRow) {
    const get = () => FileLibraryAPI.downloadUrl(slug, projectId, f.id)
      .then((r) => unwrap<{ download_url: string }>(r));
    try {
      let url: string;
      try { url = (await get()).download_url; }
      catch { url = (await get()).download_url; } // C.118：失败自动重申一次预签名
      window.open(url, "_blank", "noopener");
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "下载失败，请重试", "error");
    }
  }

  async function submitRename(f: LibraryFileRow, v: string) {
    // 同层同名即时校验（C.116：红框 + 提示；BR-02 后端允许共存——前端沿用原型拦截口径）
    if (files.some((x) => x.id !== f.id && x.name === v)) {
      setRenameErr("同层已存在同名文件");
      return;
    }
    try {
      await FileLibraryAPI.patchFile(slug, projectId, f.id, { name: v });
      setRenameId(null); setRenameErr(null);
      toast("已重命名", "ok");
      reloadFiles();
    } catch (e: unknown) {
      setRenameErr(e instanceof Error ? e.message : "重命名失败");
    }
  }

  async function doMove(target: string | null) {
    if (!moveFor) return;
    try {
      if (moveFor.kind === "file") {
        await FileLibraryAPI.patchFile(slug, projectId, moveFor.row.id, { folder_id: target });
      } else {
        await FileLibraryAPI.patchFolder(slug, projectId, moveFor.row.id, { parent_id: target });
      }
      const label = target ? byId.get(target)?.name ?? "目标目录" : "项目文件（根）";
      toast(`已移动到「${label}」（元数据操作，对象零拷贝）`, "ok");
      setMoveFor(null);
      reloadTree(); reloadFiles();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "移动失败", "error");
    }
  }

  async function doAttach(issueId: string, issueKey: string) {
    if (!attachFor) return;
    try {
      await FileLibraryAPI.patchFile(slug, projectId, attachFor.id, { issue_id: issueId });
      toast(`已附加到 ${issueKey}（任务附件区可见，互不复制）`, "ok");
      setAttachFor(null);
      reloadFiles();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "附加失败", "error");
    }
  }

  async function saveVisibility(vis: string, allowed: string[]) {
    if (!visFor) return;
    try {
      if (visFor.kind === "file") {
        await FileLibraryAPI.patchFile(slug, projectId, visFor.row.id, { visibility: vis as LibraryFileRow["visibility"], allowed_members: allowed });
      } else {
        await FileLibraryAPI.patchFolder(slug, projectId, visFor.row.id, { visibility: vis as FileFolderRow["visibility"], allowed_members: allowed });
      }
      setVisFor(null);
      toast("可见性已更新（三层一致）", "ok");
      reloadTree(); reloadFiles(); reloadTrashCount();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    }
  }

  async function doDelete() {
    if (!delFor) return;
    const { kind, row } = delFor;
    setDelFor(null);
    try {
      if (kind === "file") {
        await FileLibraryAPI.delFile(slug, projectId, row.id);
        toast("已移入回收站（软删，30 天）", "ok");
      } else {
        const r = await FileLibraryAPI.deleteFolder(slug, projectId, row.id);
        const res = unwrap<{ folders_deleted: number; files_deleted: number }>(r);
        toast(`已删除目录「${row.name}」（整树 ${res?.folders_deleted ?? 1} 个目录 / ${res?.files_deleted ?? 0} 个文件入回收站）`, "ok");
        // 选中目录在被删子树内 → 回根
        if (curFolderId && descendantsOf(row.id).has(curFolderId)) setCurFolderId(null);
      }
      reloadTree(); reloadFiles(); reloadTrashCount(); reloadStorage();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  async function createFolder(name: string) {
    try {
      const r = await FileLibraryAPI.createFolder(slug, projectId, { name, parent_id: curFolderId });
      const row = unwrap<FileFolderRow>(r);
      setNewDirOpen(false);
      toast(`已创建目录「${name}」`, "ok");
      reloadTree();
      if (row?.id) setCurFolderId(row.id);
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "创建失败", "error");
      setNewDirOpen(false);
    }
  }

  async function createTemplate(name: string) {
    // C.118 空文件库三模板一键创建（§7.2-1）
    if (folders.some((f) => f.parent_id === null && f.name === name)) {
      toast(`目录「${name}」已存在`, "info");
      return;
    }
    try {
      await FileLibraryAPI.createFolder(slug, projectId, { name, parent_id: null });
      toast(`已创建示例目录「${name}」`, "ok");
      reloadTree();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "创建失败", "error");
    }
  }

  async function renameFolder(name: string) {
    if (!renameFolderFor) return;
    try {
      await FileLibraryAPI.patchFolder(slug, projectId, renameFolderFor.id, { name });
      setRenameFolderFor(null);
      toast("已重命名目录", "ok");
      reloadTree();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "重命名失败", "error");
      setRenameFolderFor(null);
    }
  }

  // ── ⋯ 菜单（C.116：七操作 + 分享两项 T4-11 占位 noop）──
  const fileMenuItems: PopItem[] = [
    { key: "download", label: "⬇ 下载" },
    { key: "rename", label: "✏️ 重命名" },
    { key: "move", label: "📦 移动到…" },
    { key: "attach", label: "🔗 附加到任务…" },
    { key: "sep1", label: "", sep: true },
    { key: "share", label: "📤 分享…", todo: true },
    { key: "sharemgr", label: "🗂 分享管理", todo: true },
    ...(isAdmin ? [{ key: "vis", label: "👁 可见性…" }] : []),
    { key: "sep2", label: "", sep: true },
    { key: "del", label: "🗑 删除（回收站 30 天）", danger: true },
  ];
  const folderMenuItems: PopItem[] = [
    { key: "rename", label: "✏️ 重命名" },
    { key: "move", label: "📦 移动到…" },
    ...(isAdmin ? [{ key: "vis", label: "👁 可见性…" }] : []),
    { key: "sep", label: "", sep: true },
    { key: "del", label: "🗑 删除目录", danger: true },
  ];

  function onMenuPick(key: string) {
    const m = menuFor;
    setMenuFor(null);
    if (!m) return;
    if (m.kind === "file") {
      if (key === "download") void downloadFile(m.row);
      else if (key === "rename") { setRenameId(m.row.id); setRenameErr(null); }
      else if (key === "move") setMoveFor(m);
      else if (key === "attach") setAttachFor(m.row);
      else if (key === "vis") setVisFor(m);
      else if (key === "del") setDelFor(m);
    } else {
      if (key === "rename") setRenameFolderFor(m.row);
      else if (key === "move") setMoveFor(m);
      else if (key === "vis") setVisFor(m);
      else if (key === "del") setDelFor(m);
    }
  }

  // ── 渲染 ──
  const treeRow = (f: FileFolderRow) => {
    const active = f.id === curFolderId;
    const hasKids = (childrenOf.get(f.id)?.length ?? 0) > 0;
    return (
      <div key={f.id} className="relative">
        <div role="treeitem" aria-expanded={hasKids} aria-selected={active} data-sb-scope="files-tree-item" data-folder-id={f.id}
          onClick={() => { setCurFolderId(f.id); setRenameId(null); }}
          style={{ paddingLeft: 8 + (f.depth - 1) * 16 }}
          className={`group flex items-center gap-1.5 min-h-[30px] py-0.5 pr-1.5 rounded-md text-[13px] cursor-pointer relative
            ${active ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`}>
          {active && <span className="absolute left-0 top-1 bottom-1 w-[3px] rounded-sm bg-brand-500" aria-hidden="true" />}
          <span aria-hidden="true">📂</span>
          <span className="flex-1 min-w-0 truncate" title={f.name}>{f.name}</span>
          {f.visibility === "admins" && <span title="仅管理员可见" aria-label="仅管理员可见">🔒</span>}
          {f.visibility === "members" && <span title="指定成员可见" aria-label="指定成员可见">👥</span>}
          {f.file_count > 0 && <span className="text-[11px] text-neutral-400">{f.file_count}</span>}
          <span className="relative">
            <button type="button" aria-label={`目录操作 ${f.name}`} data-folder-menu={f.id}
              onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor ? null : { kind: "folder", row: f }); }}
              className="opacity-0 group-hover:opacity-100 w-[22px] h-[22px] rounded text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">⋯</button>
            {menuFor?.kind === "folder" && menuFor.row.id === f.id && (
              <PopoverMenu items={folderMenuItems} onPick={onMenuPick} close={() => setMenuFor(null)} />
            )}
          </span>
        </div>
        {(childrenOf.get(f.id) ?? []).map(treeRow)}
      </div>
    );
  };

  const usedGB = storage ? storage.used_bytes / 1024 ** 3 : 0;
  const quotaGB = storage ? storage.quota_bytes / 1024 ** 3 : 0;
  const pct = storage && storage.quota_bytes > 0 ? Math.round((storage.used_bytes / storage.quota_bytes) * 100) : 0;

  const uploaderLabel = uploaderFilter ? memberName(uploaderFilter) : null;
  const typeLabel = typeFilter ? TYPE_FILTERS.find((t) => t.key === typeFilter)?.label ?? typeFilter : null;

  const nameCell = (f: LibraryFileRow) => {
    if (renameId === f.id) {
      return (
        <input defaultValue={f.name} autoFocus aria-label="重命名" data-sb-scope="files-rename-input"
          aria-invalid={Boolean(renameErr)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { const v = (e.target as HTMLInputElement).value.trim(); if (v) void submitRename(f, v); }
            if (e.key === "Escape") { setRenameId(null); setRenameErr(null); }
          }}
          onBlur={(e) => { if (renameErr) return; const v = e.target.value.trim(); if (v && v !== f.name) void submitRename(f, v); else { setRenameId(null); } }}
          className={`h-7 px-2 rounded border text-[13px] outline-none w-full max-w-[320px] ${renameErr ? "border-red-500" : "border-brand-400"}`} />
      );
    }
    return (
      <span className="flex items-center gap-1.5 min-w-0">
        {/* 预览入口：抽屉挂点归 T4-11（FILE-003）——本任务点击不崩即可 */}
        <button type="button" className="truncate text-[13px] text-neutral-900 hover:underline" data-todo="t4-11"
          title={`${f.name}（预览即将开放）`} onClick={() => { /* noop：T4-11 抽屉挂点 */ }}>{f.name}</button>
        {f.visibility === "admins" && <span title="仅管理员可见" aria-label="仅管理员可见">🔒</span>}
        {f.visibility === "members" && <span title="指定成员可见" aria-label="指定成员可见">👥</span>}
      </span>
    );
  };

  const listBody = (
    <table className="w-full border-collapse text-[13px]" data-sb-scope="files-table">
      <thead>
        <tr>
          <th className="w-10 text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">类型</th>
          <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">名称</th>
          <th className="w-[90px] text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">大小</th>
          <th className="w-[110px] text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">上传人</th>
          <th className="w-[90px] text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">修改时间</th>
          <th className="w-[60px] text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">操作</th>
        </tr>
      </thead>
      <tbody>
        {visibleRows.map((f) => (
          <tr key={f.id} data-sb-scope="files-row" data-file-id={f.id} className="group hover:bg-neutral-50">
            <td className="px-2 py-1.5 border-b border-neutral-100">
              <span role="img" aria-label={fileTypeName(f.type_category, f.name)} title={fileTypeName(f.type_category, f.name)}>
                {fileIcon(f.type_category, f.name)}
              </span>
            </td>
            <td className="px-2 py-1.5 border-b border-neutral-100">
              {nameCell(f)}
              {renameId === f.id && renameErr && (
                <span role="alert" className="block text-[11.5px] text-red-600 mt-0.5" data-sb-scope="files-rename-err">{renameErr}</span>
              )}
            </td>
            <td className="px-2 py-1.5 border-b border-neutral-100 font-mono text-neutral-500 whitespace-nowrap">{humanSize(f.size_bytes)}</td>
            <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-600 whitespace-nowrap">
              {f.uploaded_by_detail?.display_name ?? memberName(f.uploaded_by)}
            </td>
            <td className="px-2 py-1.5 border-b border-neutral-100 font-mono text-neutral-500 whitespace-nowrap">{shortDate(f.updated_at)}</td>
            <td className="px-2 py-1.5 border-b border-neutral-100">
              <span className="relative inline-block">
                <button type="button" aria-label={`更多操作 ${f.name}`} data-file-menu={f.id}
                  onClick={() => setMenuFor(menuFor?.kind === "file" && (menuFor as { row: { id: string } }).row.id === f.id ? null : { kind: "file", row: f })}
                  className="w-6 h-6 rounded text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">⋯</button>
                {menuFor?.kind === "file" && menuFor.row.id === f.id && (
                  <PopoverMenu items={fileMenuItems} onPick={onMenuPick} close={() => setMenuFor(null)} />
                )}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  const gridBody = (
    <div className="grid gap-3 pt-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }} data-sb-scope="files-grid">
      {visibleRows.map((f) => (
        <div key={f.id} role="button" tabIndex={0} aria-label={`${f.name}，${humanSize(f.size_bytes)}`}
          data-sb-scope="files-card" data-file-id={f.id} data-todo="t4-11"
          onClick={() => { /* 预览抽屉归 T4-11 */ }}
          className="border border-neutral-200 rounded-xl p-2.5 flex flex-col gap-1.5 cursor-pointer bg-white hover:border-brand-400 hover:shadow-sm">
          <div className="h-[78px] rounded-md bg-neutral-100 flex items-center justify-center text-[30px] text-neutral-400" aria-hidden="true">
            {fileIcon(f.type_category, f.name)}
          </div>
          <div className="text-[12.5px] text-neutral-900 line-clamp-2 break-all" title={f.name}>{f.name}</div>
          <div className="text-[11px] text-neutral-400">{humanSize(f.size_bytes)} · {f.uploaded_by_detail?.display_name ?? memberName(f.uploaded_by)}</div>
        </div>
      ))}
    </div>
  );

  const bodyContent = () => {
    if (!treeLoading && folders.length === 0) {
      // 空文件库（C.118 §3.4：首次引导卡三模板一键创建）
      return (
        <div className="flex-1 flex items-center justify-center" data-sb-scope="files-empty-lib">
          <div className="w-[460px] bg-white border border-neutral-200 rounded-2xl p-8 text-center">
            <div className="text-[40px]" aria-hidden="true">📁</div>
            <div className="text-[16px] font-semibold text-neutral-800 mt-2">欢迎使用项目文件库</div>
            <div className="text-[13px] text-neutral-500 mt-1">从示例目录模板开始，或拖入第一批文件</div>
            <div className="flex gap-2.5 justify-center mt-5 flex-wrap">
              {["需求文档", "设计稿", "会议纪要"].map((n) => (
                <button key={n} type="button" data-sb-scope="files-tpl" data-tpl={n} onClick={() => { void createTemplate(n); }}
                  className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">📂 {n}</button>
              ))}
            </div>
          </div>
        </div>
      );
    }
    if (!curFolderId) {
      // 根层引导（偏差：根层无文件列表端点——§4.2 #5 仅 folders/{id}/files/）
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-neutral-500" data-sb-scope="files-root-guide">
          <div className="text-[40px]" aria-hidden="true">📂</div>
          <div className="text-[15px] font-semibold text-neutral-700">项目文件</div>
          <div className="text-[13px]">选择左侧目录浏览或上传文件（文件需存放于目录中）</div>
          {canUpload && (
            <button type="button" onClick={() => setNewDirOpen(true)} data-sb-scope="files-newdir-empty"
              className="mt-2 h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white">＋ 新建目录</button>
          )}
        </div>
      );
    }
    if (loadingFiles) {
      return <div className="py-8 space-y-2">{[0, 1, 2, 3].map((i) => <div key={i} className="h-9 rounded bg-neutral-100 animate-pulse" />)}</div>;
    }
    if (files.length === 0 && !anyFilter) {
      // 空目录（C.118：拖拽文件到此处，或点击上传——含目标目录名）
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-neutral-500" data-sb-scope="files-empty-folder">
          <div className="text-[40px]" aria-hidden="true">📂</div>
          <div className="text-[15px] font-semibold text-neutral-700">拖拽文件到此处，或点击上传</div>
          <div className="text-[13px]">上传到「{curFolder?.name ?? ""}」· 大于 50MB 自动分片续传（FILE-003）</div>
          <button type="button" disabled={!canUpload} onClick={() => fileInputRef.current?.click()} data-sb-scope="files-upload-empty"
            title={canUpload ? undefined : "访客只读（file.upload 403）"}
            className="mt-2 h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed text-[13px] text-white">＋ 上传</button>
        </div>
      );
    }
    if (visibleRows.length === 0 && anyFilter) {
      // 过滤无结果（C.118：未找到匹配文件 + 清除筛选）
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-1.5 text-neutral-500" data-sb-scope="files-empty-filter">
          <div className="text-[15px] font-semibold text-neutral-700">未找到匹配文件</div>
          <div className="text-[13px]">调整筛选条件，或
            <button type="button" className="text-brand-600 hover:underline ml-0.5" data-sb-scope="files-clear-filter"
              onClick={() => { setNameInput(""); setNameFilter(""); setTypeFilter(""); setUploaderFilter(""); setTimeFilter(""); }}>清除全部筛选</button>
          </div>
        </div>
      );
    }
    return (
      <div className="flex-1 overflow-y-auto px-0 pb-4">
        {view === "list" ? listBody : gridBody}
        <div className="text-center text-neutral-400 text-[12px] py-4" aria-hidden="true">⤓ 拖拽文件到此处上传到「{curFolder?.name ?? ""}」</div>
      </div>
    );
  };

  const inFlight = uploads.filter((u) => u.state === "uploading").length;

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden"
      onDragOver={(e) => { e.preventDefault(); if (canUpload && curFolderId) setDropOn(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropOn(false); }}
      onDrop={(e) => {
        e.preventDefault(); setDropOn(false);
        const fs = Array.from(e.dataTransfer.files);
        if (fs.length) startUpload(fs);
      }}>
      {/* ── 左树（C.112：缩进 16px/层、当前高亮 ▌、悬浮 ⋯、role=tree）── */}
      <div className="w-[260px] shrink-0 border-r border-neutral-200 p-3 overflow-auto bg-white" role="tree" aria-label="目录树" data-sb-scope="files-tree">
        <div role="treeitem" aria-selected={curFolderId === null} data-sb-scope="files-tree-root" onClick={() => { setCurFolderId(null); setRenameId(null); }}
          className={`relative flex items-center gap-1.5 min-h-[30px] px-2 py-0.5 rounded-md text-[13px] cursor-pointer mb-0.5
            ${curFolderId === null ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`}>
          {curFolderId === null && <span className="absolute left-0 top-1 bottom-1 w-[3px] rounded-sm bg-brand-500" aria-hidden="true" />}
          <span aria-hidden="true">📂</span><span className="flex-1 min-w-0 truncate">项目文件</span>
        </div>
        {treeLoading ? (
          // 树加载骨架（C.118：3 级骨架）
          <div className="space-y-1.5 py-1" data-sb-scope="files-tree-skeleton" aria-hidden="true">
            <div className="h-7 rounded bg-neutral-100 animate-pulse" />
            <div className="h-7 rounded bg-neutral-100 animate-pulse ml-4" />
            <div className="h-7 rounded bg-neutral-100 animate-pulse ml-8" />
            <div className="h-7 rounded bg-neutral-100 animate-pulse" />
            <div className="h-7 rounded bg-neutral-100 animate-pulse ml-4" />
          </div>
        ) : (childrenOf.get(null) ?? []).map(treeRow)}
        {canUpload && !treeLoading && (
          <button type="button" onClick={() => setNewDirOpen(true)} data-sb-scope="files-newdir"
            className="w-full mt-3.5 h-9 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">＋ 新建目录</button>
        )}
        <div role="treeitem" onClick={() => navigate(`/${slug}/projects/${projectId}/files/trash`)}
          data-sb-scope="files-trash-entry"
          className="mt-1.5 flex items-center gap-1.5 min-h-[30px] px-2 py-0.5 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50 cursor-pointer">
          <span aria-hidden="true">🗑</span><span className="flex-1 min-w-0 truncate">回收站</span>
          {trashCount !== null && trashCount > 0 && <span className="text-[11px] text-neutral-400" data-sb-scope="files-trash-count">{trashCount}</span>}
        </div>
      </div>

      {/* ── 右区（面包屑 / 工具条 / 双视图 / dropzone / 配额条）── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden relative">
        <div className="flex items-center gap-1 px-5 pt-2.5 text-[13px] text-neutral-500 shrink-0" aria-label="面包屑" data-sb-scope="files-crumb">
          <button type="button" className="text-neutral-600 hover:underline" onClick={() => setCurFolderId(null)}>项目文件</button>
          {pathChain.map((c) => (
            <span key={c.id} className="flex items-center gap-1">
              <span aria-hidden="true">›</span>
              <button type="button" className="text-neutral-600 hover:underline" onClick={() => setCurFolderId(c.id)}>{c.name}</button>
            </span>
          ))}
          {curFolder && (
            <span className="flex items-center gap-1">
              <span aria-hidden="true">›</span>
              <span className="font-medium text-neutral-800">{curFolder.name}</span>
            </span>
          )}
          <span className="ml-auto text-[12px] text-neutral-400" data-sb-scope="files-count">
            {curFolderId
              ? `${visibleRows.length} 个文件${filesMeta?.total_size_bytes ? ` · ${humanSize(filesMeta.total_size_bytes)}` : ""}`
              : `${folders.reduce((s, f) => s + f.file_count, 0)} 个文件`}
          </span>
        </div>

        <div className="flex items-center gap-2 px-5 py-2 shrink-0 flex-wrap" data-sb-scope="files-toolbar">
          <div className="h-8 border border-neutral-300 rounded-md inline-flex items-center gap-1.5 px-2.5 bg-white min-w-[180px]">
            <span aria-hidden="true" className="text-neutral-400">🔍</span>
            <input value={nameInput} onChange={(e) => setNameInput(e.target.value)} placeholder="名称过滤（防抖 300ms）"
              aria-label="名称过滤" data-sb-scope="files-filter-name"
              className="border-none outline-none bg-transparent text-[13px] text-neutral-800 flex-1 w-24" />
          </div>
          <div className="relative">
            <button type="button" onClick={() => setFilterMenu(filterMenu === "type" ? null : "type")} data-sb-scope="files-filter-type"
              aria-haspopup="menu" aria-expanded={filterMenu === "type"}
              className={`h-8 px-2.5 rounded-md border text-[13px] inline-flex items-center gap-1.5 ${typeFilter ? "border-brand-500 text-brand-600 bg-brand-50" : "border-neutral-300 text-neutral-600 hover:bg-neutral-50 bg-white"}`}>
              类型{typeLabel ? ` · ${typeLabel}` : ""} ▾
            </button>
            {filterMenu === "type" && (
              <PopoverMenu items={TYPE_FILTERS.map((t) => ({ key: t.key || "all", label: `${t.label}${(typeFilter || "") === (t.key || "") ? " ✓" : ""}` }))}
                onPick={(k) => { setTypeFilter(k === "all" ? "" : k); setFilterMenu(null); }} close={() => setFilterMenu(null)} />
            )}
          </div>
          <div className="relative">
            <button type="button" onClick={() => setFilterMenu(filterMenu === "uploader" ? null : "uploader")} data-sb-scope="files-filter-uploader"
              aria-haspopup="menu" aria-expanded={filterMenu === "uploader"}
              className={`h-8 px-2.5 rounded-md border text-[13px] inline-flex items-center gap-1.5 ${uploaderFilter ? "border-brand-500 text-brand-600 bg-brand-50" : "border-neutral-300 text-neutral-600 hover:bg-neutral-50 bg-white"}`}>
              上传人{uploaderLabel ? ` · ${uploaderLabel}` : ""} ▾
            </button>
            {filterMenu === "uploader" && (
              <PopoverMenu items={[
                { key: "all", label: `全部上传人${!uploaderFilter ? " ✓" : ""}` },
                ...members.map((m) => ({ key: m.id, label: `${m.name}${uploaderFilter === m.id ? " ✓" : ""}` })),
              ]} onPick={(k) => { setUploaderFilter(k === "all" ? "" : k); setFilterMenu(null); }} close={() => setFilterMenu(null)} />
            )}
          </div>
          <div className="relative">
            <button type="button" onClick={() => setFilterMenu(filterMenu === "time" ? null : "time")} data-sb-scope="files-filter-time"
              aria-haspopup="menu" aria-expanded={filterMenu === "time"}
              className={`h-8 px-2.5 rounded-md border text-[13px] inline-flex items-center gap-1.5 ${timeFilter ? "border-brand-500 text-brand-600 bg-brand-50" : "border-neutral-300 text-neutral-600 hover:bg-neutral-50 bg-white"}`}>
              时间{timeFilter === "week" ? " · 最近一周" : ""} ▾
            </button>
            {filterMenu === "time" && (
              <PopoverMenu items={[
                { key: "all", label: `全部时间${!timeFilter ? " ✓" : ""}` },
                { key: "week", label: `最近一周${timeFilter === "week" ? " ✓" : ""}` },
              ]} onPick={(k) => { setTimeFilter(k === "all" ? "" : k); setFilterMenu(null); }} close={() => setFilterMenu(null)} />
            )}
          </div>
          <div className="flex-1" />
          <div className="inline-flex bg-neutral-100 rounded-lg p-0.5 gap-0.5" role="tablist" aria-label="视图切换">
            <button type="button" role="tab" aria-selected={view === "list"} data-sb-scope="files-view-list" onClick={() => setView("list")}
              className={`h-8 px-2.5 rounded-md text-[13px] ${view === "list" ? "bg-white shadow-sm text-neutral-800 font-medium" : "text-neutral-500"}`}>☰ 列表</button>
            <button type="button" role="tab" aria-selected={view === "grid"} data-sb-scope="files-view-grid" onClick={() => setView("grid")}
              className={`h-8 px-2.5 rounded-md text-[13px] ${view === "grid" ? "bg-white shadow-sm text-neutral-800 font-medium" : "text-neutral-500"}`}>▦ 网格</button>
          </div>
          <input ref={fileInputRef} type="file" multiple hidden data-sb-scope="files-upload-input"
            onChange={(e) => { const fs = Array.from(e.target.files ?? []); e.target.value = ""; if (fs.length) startUpload(fs); }} />
          <button type="button" disabled={!canUpload} onClick={() => fileInputRef.current?.click()} data-sb-scope="files-upload-btn"
            title={canUpload ? undefined : "访客只读（file.upload 403）"}
            className="h-8 px-3 rounded-md bg-brand-500 hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed text-[13px] text-white">＋ 上传</button>
        </div>

        <div className="flex-1 min-h-0 flex flex-col px-5" data-sb-scope="files-body">
          {bodyContent()}
        </div>

        {/* 拖入高亮（C.114：蓝色虚线框 + 「松开上传到 {目录}」） */}
        {dropOn && (
          <div className="absolute inset-3 border-2 border-dashed border-brand-500 rounded-xl bg-brand-500/5 flex items-center justify-center text-[14px] text-brand-600 pointer-events-none z-20"
            data-sb-scope="files-dropzone-hint">松开上传到「{curFolder?.name ?? "项目文件"}」</div>
        )}

        {/* 上传进度浮层（C.115：role=status aria-live=polite + 里程碑播报 + 失败红 + 重试 + 取消） */}
        {uploads.length > 0 && (
          <div className="absolute right-5 bottom-14 w-[360px] bg-white border border-neutral-200 rounded-xl shadow-lg z-30 overflow-hidden"
            role="status" aria-live="polite" data-sb-scope="files-uploads">
            <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-neutral-200 text-[13px] font-medium">
              ⬆ 上传中（{inFlight}）
              <span className="sr-only" data-sb-scope="files-upload-announce">{announce}</span>
              <button type="button" aria-label="收起" onClick={() => setUploads([])} className="ml-auto w-6 h-6 rounded text-neutral-400 hover:bg-neutral-100">✕</button>
            </div>
            {uploads.map((u) => (
              <div key={u.key} className={`px-3.5 py-2 flex flex-col gap-1.5 ${u.state === "failed" ? "bg-red-50/60" : ""}`} data-sb-scope="files-upload-row" data-upload-state={u.state}>
                <div className="flex items-center gap-2 text-[12.5px] text-neutral-700">
                  <span aria-hidden="true">{u.state === "failed" ? "⚠" : u.state === "done" ? "✓" : "⬆"}</span>
                  <span className="flex-1 min-w-0 truncate">{u.name}</span>
                  {u.state === "uploading" ? (
                    <button type="button" aria-label={`取消 ${u.name}`} data-sb-scope="files-upload-cancel" onClick={() => cancelUpload(u.key)}
                      className="w-[22px] h-[22px] rounded text-neutral-400 hover:bg-neutral-100">✕</button>
                  ) : u.state === "failed" ? (
                    <button type="button" data-sb-scope="files-upload-retry" onClick={() => retryUpload(u)}
                      className="h-6 px-2 rounded border border-neutral-300 text-[12px] text-neutral-600 hover:bg-white">重试</button>
                  ) : null}
                </div>
                <div className="h-1 rounded-full bg-neutral-100 overflow-hidden">
                  <div className={`h-full rounded-full transition-[width] duration-300 ${u.state === "failed" ? "bg-red-500" : u.state === "done" ? "bg-emerald-500" : "bg-brand-500"}`}
                    style={{ width: `${u.pct}%` }} role="progressbar" aria-valuenow={u.pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${u.name} 上传进度`} />
                </div>
                <div className="flex justify-between text-[11px] text-neutral-400">
                  <span>{u.pct}% · {u.speed}</span>
                  <span>{u.statusText}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 配额条（C.115：warn/full 分色） */}
        <div className={`flex items-center gap-2.5 px-5 py-1.5 text-[12px] border-t border-neutral-200 shrink-0 ${pct >= 98 ? "text-red-600" : pct >= 95 ? "text-amber-600" : "text-neutral-500"}`}
          data-sb-scope="files-quota" data-quota-state={pct >= 98 ? "full" : pct >= 95 ? "warn" : "ok"}>
          <span>📦 工作区存储</span>
          <div className="flex-1 max-w-[280px] h-[5px] rounded-full bg-neutral-100 overflow-hidden">
            <div className={`h-full rounded-full ${pct >= 98 ? "bg-red-500" : pct >= 95 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, pct)}%` }} />
          </div>
          <span className="font-mono">{usedGB.toFixed(1)} / {quotaGB.toFixed(0)} GB（{pct}%）</span>
          {pct >= 95 && <span className="text-red-600">配额将满</span>}
        </div>
      </div>

      {/* ── 弹层（C.116）── */}
      {newDirOpen && (
        <NameInputDialog title="新建目录" initial="" confirmText="创建"
          validate={(v) => (folders.some((f) => f.parent_id === curFolderId && f.name === v) ? "同层已存在同名目录" : v.length > 64 ? "目录名最多 64 字符" : null)}
          onSubmit={(v) => { void createFolder(v); }} onClose={() => setNewDirOpen(false)} />
      )}
      {renameFolderFor && (
        <NameInputDialog title="重命名目录" initial={renameFolderFor.name} confirmText="保存"
          validate={(v) => (folders.some((f) => f.id !== renameFolderFor.id && f.parent_id === renameFolderFor.parent_id && f.name === v) ? "同层已存在同名目录" : v.length > 64 ? "目录名最多 64 字符" : null)}
          onSubmit={(v) => { void renameFolder(v); }} onClose={() => setRenameFolderFor(null)} />
      )}
      {moveFor && (
        <MoveTargetPicker name={moveFor.row.name} folders={folders}
          disabledIds={moveFor.kind === "folder" ? descendantsOf(moveFor.row.id) : new Set<string>()}
          onPick={(t) => { void doMove(t); }} onClose={() => setMoveFor(null)} />
      )}
      {attachFor && (
        <AttachToTaskModal slug={slug} projectId={projectId} fileName={attachFor.name}
          onPick={(id, key) => { void doAttach(id, key); }} onClose={() => setAttachFor(null)} />
      )}
      {visFor && (
        <VisibilityEditor name={visFor.row.name} current={visFor.row.visibility}
          allowedMembers={visFor.kind === "folder" ? visFor.row.allowed_members ?? [] : []}
          members={members} onSave={(v, allowed) => { void saveVisibility(v, allowed); }} onClose={() => setVisFor(null)} />
      )}
      {delFor && (
        <ConfirmDialog
          title={delFor.kind === "folder" ? "删除目录" : "删除文件"}
          danger okText="删除" onOk={() => { void doDelete(); }} onClose={() => setDelFor(null)}>
          {delFor.kind === "folder"
            ? <>目录内含 <b className="font-mono">{delFor.row.file_count}</b> 个可见文件，将一并移入回收站。回收站保留 30 天，期满自动清理。</>
            : <>「{delFor.row.name}」将移入回收站，30 天后自动清理。</>}
        </ConfirmDialog>
      )}
      {quotaConfirm && (
        <QuotaConfirmDialog used={quotaConfirm.used} quota={quotaConfirm.quota} incoming={quotaConfirm.incoming}
          onProceed={() => { const b = quotaConfirm.batch; const fid = curFolderId; setQuotaConfirm(null); if (fid) b.forEach((f) => { void uploadOne(f, fid); }); }}
          onClose={() => setQuotaConfirm(null)} />
      )}
    </div>
  );
}

/** FILE-002 §3.1 回收站页（C.117）：R1 口径（CONTRIBUTOR 仅本人——后端过滤、前端如实
 *  渲染 + 角色口径提示文案）+ 还原（冲突落根说明）+ 彻底删除（仅 ADMIN）+ 剩余天数。 */
export function FileTrash({ slug, projectId, canUpload, isAdmin }: {
  slug: string; projectId: string; canUpload: boolean; isAdmin: boolean;
}) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<LibraryFileRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState(false);
  const [folders, setFolders] = useState<FileFolderRow[]>([]);
  const [members, setMembers] = useState<Array<{ id: string; name: string }>>([]);
  const [restoreFor, setRestoreFor] = useState<LibraryFileRow | null>(null);
  const [purgeFor, setPurgeFor] = useState<LibraryFileRow | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    FileLibraryAPI.trash(slug, projectId, { per_page: 100 })
      .then((r) => { setRows(unwrap<LibraryFileRow[]>(r) ?? []); setDenied(false); })
      .catch(() => { setRows([]); setDenied(true); })
      .finally(() => setLoading(false));
  }, [slug, projectId]);

  useEffect(() => {
    const t = setTimeout(() => { reload(); }, 0); // setState-in-effect 规避
    return () => clearTimeout(t);
  }, [reload]);
  useEffect(() => {
    FileLibraryAPI.folders(slug, projectId).then((r) => { setFolders(unwrap<FileFolderRow[]>(r) ?? []); }).catch(() => {});
    ProjectMemberAPI.list(slug, projectId, { per_page: 100 })
      .then((r) => {
        const ms = unwrap<Array<{ user: { id: string; display_name: string } }>>(r) ?? [];
        setMembers(ms.map((x) => ({ id: x.user.id, name: x.user.display_name })));
      })
      .catch(() => {});
  }, [slug, projectId]);

  const byId = useMemo(() => new Map(folders.map((f) => [f.id, f])), [folders]);
  const pathOf = useCallback((folderId: string | null) => {
    if (!folderId) return "根目录";
    const segs: string[] = [];
    let cur = byId.get(folderId);
    while (cur) { segs.unshift(cur.name); cur = cur.parent_id ? byId.get(cur.parent_id) : undefined; }
    return segs.length ? segs.join("/") : "已删除目录";
  }, [byId]);
  const memberName = useCallback((uid: string | null | undefined) => {
    if (!uid) return "—";
    return members.find((m) => m.id === uid)?.name ?? "…";
  }, [members]);

  async function doRestore() {
    if (!restoreFor) return;
    const row = restoreFor;
    setRestoreFor(null);
    try {
      await FileLibraryAPI.restore(slug, projectId, row.id);
      toast("已还原（对象零拷贝，仅元数据）", "ok");
      reload();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "还原失败", "error");
    }
  }

  async function doPurge() {
    if (!purgeFor) return;
    const row = purgeFor;
    setPurgeFor(null);
    try {
      await FileLibraryAPI.purge(slug, projectId, row.id);
      toast("已彻底删除（引用计数归零后清对象）", "ok");
      reload();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "彻底删除失败", "error");
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="flex items-center gap-1 px-5 pt-2.5 text-[13px] text-neutral-500 shrink-0" data-sb-scope="trash-crumb">
        <button type="button" className="text-neutral-600 hover:underline" onClick={() => navigate(`/${slug}/projects/${projectId}/files`)}>项目文件</button>
        <span aria-hidden="true">›</span>
        <span className="font-medium text-neutral-800">回收站</span>
        <span className="ml-auto text-[12px] text-neutral-400">
          {isAdmin ? "管理员视角：全量可见" : "贡献者视角：仅本人删除项（BR-13）"} · 30 天后自动清理
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-5 py-2" data-sb-scope="trash-body">
        {denied ? (
          <div className="flex flex-col items-center justify-center py-16 text-neutral-500" data-sb-scope="trash-denied">
            <div className="text-[15px] font-semibold text-neutral-700">无权访问回收站</div>
            <div className="text-[13px] mt-1">回收站需要 CONTRIBUTOR 及以上角色（file.delete）</div>
          </div>
        ) : loading ? (
          <div className="py-8 space-y-2">{[0, 1, 2].map((i) => <div key={i} className="h-9 rounded bg-neutral-100 animate-pulse" />)}</div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-neutral-500" data-sb-scope="trash-empty">
            <div className="text-[15px] font-semibold text-neutral-700">回收站为空</div>
          </div>
        ) : (
          <table className="w-full border-collapse text-[13px]" data-sb-scope="trash-table">
            <thead>
              <tr>
                <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">名称</th>
                <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">原位置</th>
                <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">删除人</th>
                <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">删除时间</th>
                <th className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">剩余</th>
                <th className="w-[220px] text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="hover:bg-neutral-50" data-sb-scope="trash-row" data-file-id={t.id}>
                  <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-800">{fileIcon(t.type_category, t.name)} {t.name}</td>
                  <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-500">{pathOf(t.folder_id)}</td>
                  <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-600 whitespace-nowrap">{memberName(t.uploaded_by)}</td>
                  <td className="px-2 py-1.5 border-b border-neutral-100 font-mono text-neutral-500 whitespace-nowrap">{shortDate(t.deleted_at)}</td>
                  <td className="px-2 py-1.5 border-b border-neutral-100 font-mono text-neutral-500">{trashDaysLeft(t.deleted_at)} 天</td>
                  <td className="px-2 py-1.5 border-b border-neutral-100">
                    <span className="inline-flex gap-2">
                      {canUpload && (
                        <button type="button" data-sb-scope="trash-restore" data-file-id={t.id} onClick={() => setRestoreFor(t)}
                          className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12.5px] text-neutral-600 hover:bg-neutral-50">还原</button>
                      )}
                      {isAdmin && (
                        <button type="button" data-sb-scope="trash-purge" data-file-id={t.id} onClick={() => setPurgeFor(t)}
                          className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12.5px] text-red-600 hover:bg-red-50">彻底删除</button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {restoreFor && (
        <ConfirmDialog title="还原文件" okText="还原" onOk={() => { void doRestore(); }} onClose={() => setRestoreFor(null)}>
          将还原到原位置「{pathOf(restoreFor.folder_id)}」。若原位存在同名文件，将落至根目录并加后缀 <b>(恢复)</b>。
        </ConfirmDialog>
      )}
      {purgeFor && (
        <ConfirmDialog title="彻底删除？" danger okText="彻底删除" onOk={() => { void doPurge(); }} onClose={() => setPurgeFor(null)}>
          将立即物理删除对象与行记录，不可恢复。
        </ConfirmDialog>
      )}
    </div>
  );
}

/** 页面壳取项目名/标识（Topbar + ProjectSidebar 复用；gantt.tsx 同款）。 */
export function useProjectHeader(slug?: string, projectId?: string) {
  const [name, setName] = useState("…");
  const [identifier, setIdentifier] = useState("");
  useEffect(() => {
    if (!slug || !projectId) return;
    ProjectAPI.detail(slug, projectId)
      .then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setName(d?.name ?? "…");
        setIdentifier(d?.identifier ?? "");
      })
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, projectId]);
  return { name, identifier };
}
