import { useEffect, useRef, useState } from "react";
import { IssueAPI } from "../../services/api";
import { toast } from "../Toast";
import { VIS_LABEL } from "./filelib-shared";

/** FILE-002 §3.3 ⋯ 菜单与操作弹层（C.116）+ 目录操作。
 *  菜单键盘可达（menuitem + 方向键不必需——按钮本身可 Tab/Enter）；
 *  dropdown 外击关闭走 mousedown 阶段（CLAUDE.md 教训 #4）。 */

export interface PopItem {
  key: string;
  label: string;
  danger?: boolean;
  /** T4-11 交付的占位项（分享/分享管理/预览）：渲染但点击 noop（不 toast，防污染 e2e）。 */
  todo?: boolean;
  sep?: boolean;
}

/** ⋯ 下拉菜单（原型 menuAt：定位 + mousedown 外击关闭）。 */
export function PopoverMenu({ items, onPick, close }: {
  items: PopItem[];
  onPick: (key: string) => void;
  close: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [close]);
  return (
    // onClick 阻断冒泡：弹层渲染在宿主行内（左树目录行/表格行），菜单项点击若冒泡
    // 到树行 onClick 会顺带切换当前目录（S4F-4 目录「移动到…」实测选中漂移）
    <div ref={ref} className="absolute right-0 top-full mt-1 z-40 min-w-[180px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" role="menu" data-sb-scope="files-pop"
      onClick={(e) => e.stopPropagation()}>
      {items.map((it) => it.sep ? (
        <div key={it.key} className="h-px bg-neutral-100 my-1" role="separator" />
      ) : (
        <button key={it.key} type="button" role="menuitem" data-menu-key={it.key} {...(it.todo ? { "data-todo": "t4-11" } : {})}
          onClick={() => { if (it.todo) return; onPick(it.key); }}
          className={`w-full text-left px-3 py-1.5 text-[13px] ${it.danger ? "text-red-600 hover:bg-red-50" : "text-neutral-700 hover:bg-neutral-50"}`}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

/** 通用确认弹层（role=alertdialog，C.116 删除确认 / C.117 还原与彻底删除）。 */
export function ConfirmDialog({ title, body, children, okText, danger, onOk, onClose, width = 480, layer = 50 }: {
  title: string; body?: React.ReactNode; children?: React.ReactNode; okText: string; danger?: boolean;
  onOk: () => void; onClose: () => void; width?: number;
  /** 层级：预览抽屉（z-71）内弹出的确认需抬到 z-90（mask/弹层否则被抽屉遮住）。 */
  layer?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4"
      style={{ zIndex: layer }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} data-sb-scope="files-confirm-mask">
      <div className="bg-white rounded-xl shadow-lg p-5 max-w-full" role="alertdialog" aria-modal="true" aria-label={title}
        style={{ width }} data-sb-scope="files-confirm">
        <div className="text-[15px] font-semibold mb-2.5">{title}</div>
        <div className="text-[13.5px] text-neutral-600 leading-relaxed">{body ?? children}</div>
        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
          <button type="button" onClick={onOk} data-sb-scope="files-confirm-ok"
            className={`h-9 px-3.5 rounded-md text-[13px] text-white ${danger ? "bg-red-600 hover:bg-red-700" : "bg-brand-500 hover:bg-brand-600"}`}>{okText}</button>
        </div>
      </div>
    </div>
  );
}

/** 目录/文件名输入弹层（新建目录 / 目录改名；文件改名走行内编辑）。
 *  同层同名即时校验：红框 + 行内提示（BR-01 409 的前端前置）。 */
export function NameInputDialog({ title, initial, confirmText, validate, onSubmit, onClose }: {
  title: string; initial: string; confirmText: string;
  validate: (name: string) => string | null; // 返回错误文案；null = 通过
  onSubmit: (name: string) => void; onClose: () => void;
}) {
  const [val, setVal] = useState(initial);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);
  const submit = () => {
    const v = val.trim();
    if (!v) { setErr("名称不能为空"); return; }
    const msg = validate(v);
    if (msg) { setErr(msg); return; }
    onSubmit(v);
  };
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-xl shadow-lg p-5 w-[440px] max-w-full" role="dialog" aria-modal="true" aria-label={title}
        onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") onClose(); }}>
        <div className="text-[15px] font-semibold mb-3">{title}</div>
        <input ref={inputRef} value={val} onChange={(e) => { setVal(e.target.value); setErr(null); }}
          aria-label="名称" aria-invalid={Boolean(err)}
          className={`w-full h-9 px-3 rounded-md border text-[13px] outline-none ${err ? "border-red-500" : "border-neutral-300 focus:border-brand-400"}`}
          data-sb-scope="files-name-input" />
        {err && <div role="alert" className="text-[12.5px] text-red-600 mt-1.5" data-sb-scope="files-name-err">{err}</div>}
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
          <button type="button" onClick={submit} className="h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white" data-sb-scope="files-name-ok">{confirmText}</button>
        </div>
      </div>
    </div>
  );
}

/** 移动选择器（C.116：目录树 + 禁选自身后代（置灰+提示）+ 显示目标路径；
 *  文件可落任意目录；移动为毫秒级元数据操作——提示行）。 */
export function MoveTargetPicker({ name, folders, disabledIds, onPick, onClose }: {
  name: string;
  folders: Array<{ id: string; name: string; parent_id: string | null; depth: number }>;
  disabledIds: Set<string>;
  onPick: (folderId: string | null) => void; // null = 根
  onClose: () => void;
}) {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const pathOf = (id: string | null): string => {
    const segs: string[] = [];
    let cur = id ? byId.get(id) : undefined;
    while (cur) { segs.unshift(cur.name); cur = cur.parent_id ? byId.get(cur.parent_id) : undefined; }
    return ["项目文件", ...segs].join(" / ");
  };
  const [hoverId, setHoverId] = useState<string | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const rows: Array<{ id: string | null; name: string; depth: number; dis: boolean }> = [
    { id: null, name: "项目文件", depth: 0, dis: disabledIds.has("") },
    ...folders.map((f) => ({ id: f.id as string | null, name: f.name, depth: f.depth, dis: disabledIds.has(f.id) })),
  ];
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-xl shadow-lg p-5 w-[520px] max-w-full" role="dialog" aria-modal="true" aria-label={`移动 ${name}`}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }} data-sb-scope="files-move">
        <div className="text-[15px] font-semibold mb-3">移动「{name}」</div>
        <div className="text-[12px] text-neutral-500 mb-1.5">目标目录（禁选自身后代）</div>
        <div className="border border-neutral-200 rounded-lg max-h-[260px] overflow-auto p-1.5">
          {rows.map((r) => (
            <button key={r.id ?? "root"} type="button" disabled={r.dis} data-moveto={r.id ?? "root"}
              onClick={() => onPick(r.id)} onMouseEnter={() => setHoverId(r.id)} onMouseLeave={() => setHoverId(null)}
              title={pathOf(r.id)}
              style={{ paddingLeft: 12 + r.depth * 16 }}
              className={`w-full flex items-center gap-2 h-8 rounded-md text-[13px] text-left ${r.dis ? "opacity-40 cursor-not-allowed" : "hover:bg-neutral-50 text-neutral-700"}`}>
              <span>📂</span><span className="truncate">{r.name}</span>
              {r.dis && <span className="ml-auto text-[11px] text-neutral-400">自身后代</span>}
            </button>
          ))}
        </div>
        <div className="text-[11.5px] text-neutral-400 mt-2" data-sb-scope="files-move-path">
          目标路径：{pathOf(hoverId)}
          <span className="block">移动为元数据操作（对象零拷贝，毫秒级）· 环与深度 &gt;5 由服务端校验</span>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
        </div>
      </div>
    </div>
  );
}

/** 附加到任务（C.116：任务搜索弹层；建立 issue 双挂——任务附件区可见、互不复制）。 */
export function AttachToTaskModal({ slug, projectId, fileName, onPick, onClose }: {
  slug: string; projectId: string; fileName: string;
  onPick: (issueId: string, issueKey: string) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Array<{ id: string; issue_key: string; name: string }>>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      IssueAPI.list(slug, projectId, { ...(q ? { q } : {}), per_page: 5 })
        .then((r) => {
          setRows(((r as unknown as { data: Array<{ id: string; issue_key: string; name: string }> }).data) ?? []);
        })
        .catch(() => setRows([]))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [slug, projectId, q]);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-xl shadow-lg p-5 w-[520px] max-w-full" role="dialog" aria-modal="true" aria-label="附加到任务"
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }} data-sb-scope="files-attach">
        <div className="text-[15px] font-semibold mb-3">附加到任务 · {fileName}</div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索任务（编号 / 标题）…"
          aria-label="搜索任务" autoFocus
          className="w-full h-9 px-3 rounded-md border border-neutral-300 focus:border-brand-400 outline-none text-[13px]" data-sb-scope="files-attach-q" />
        <div className="mt-2.5 border border-neutral-200 rounded-lg overflow-hidden">
          {loading ? (
            <div className="px-3 py-3 text-[12.5px] text-neutral-400">搜索中…</div>
          ) : rows.length === 0 ? (
            <div className="px-3 py-3 text-[12.5px] text-neutral-400">未找到任务</div>
          ) : rows.map((i) => (
            <button key={i.id} type="button" data-attachto={i.id} onClick={() => onPick(i.id, i.issue_key)}
              className="w-full flex items-center gap-2.5 px-3 h-10 text-left hover:bg-neutral-50 border-b border-neutral-100 last:border-b-0">
              <span className="badge-id">{i.issue_key}</span>
              <span className="text-[13px] text-neutral-700 truncate">{i.name}</span>
            </button>
          ))}
        </div>
        <div className="text-[11.5px] text-neutral-400 mt-2">建立 file↔issue 双挂：文件库与任务附件区互见，互不复制</div>
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
        </div>
      </div>
    </div>
  );
}

/** 可见性编辑器（C.116：三态单选 + members 态成员多选；仅 ADMIN 可达）。 */
export function VisibilityEditor({ name, current, allowedMembers, members, onSave, onClose }: {
  name: string; current: string; allowedMembers: string[];
  members: Array<{ id: string; name: string }>;
  onSave: (visibility: string, allowed: string[]) => void; onClose: () => void;
}) {
  const [vis, setVis] = useState(current);
  const [picked, setPicked] = useState<Set<string>>(new Set(allowedMembers));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const OPTIONS: Array<{ key: string; title: string; desc: string }> = [
    { key: "all", title: "👥 全员可见", desc: "项目全部成员可见" },
    { key: "members", title: "👤 指定成员", desc: "仅勾选成员与管理员可见" },
    { key: "admins", title: "🔒 仅管理员", desc: "仅项目 ADMIN 可见" },
  ];
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-xl shadow-lg p-5 w-[520px] max-w-full" role="dialog" aria-modal="true" aria-label="可见性设置"
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }} data-sb-scope="files-vis">
        <div className="text-[15px] font-semibold mb-3">可见性 · {name}</div>
        {OPTIONS.map((o) => (
          <label key={o.key} className="flex items-start gap-2.5 min-h-[44px] px-2 rounded-md hover:bg-neutral-50 cursor-pointer">
            <input type="radio" name="vis" value={o.key} checked={vis === o.key} data-vis-radio={o.key}
              onChange={() => setVis(o.key)} className="mt-1 accent-brand-500" />
            <div>
              <div className="text-[13px] text-neutral-800">{o.title}（{VIS_LABEL[o.key]}）</div>
              <div className="text-[11.5px] text-neutral-400">{o.desc}</div>
            </div>
          </label>
        ))}
        {vis === "members" && (
          <div className="ml-7 mt-1 border border-neutral-200 rounded-lg p-1.5" data-sb-scope="files-vis-members">
            {members.length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-neutral-400">暂无项目成员可选</div>
            ) : members.map((m) => (
              <label key={m.id} className="flex items-center gap-2 px-2 h-8 rounded-md hover:bg-neutral-50 cursor-pointer">
                <input type="checkbox" checked={picked.has(m.id)} data-vis-member={m.id}
                  onChange={() => setPicked((cur) => { const n = new Set(cur); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n; })}
                  className="accent-brand-500" />
                <span className="text-[13px] text-neutral-700">{m.name}</span>
              </label>
            ))}
          </div>
        )}
        <div className="text-[11.5px] text-neutral-400 mt-2.5">三态在 UI / API / 预签名三层一致校验，越权一律 404 存在性隐藏</div>
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
          <button type="button" onClick={() => onSave(vis, [...picked])} data-sb-scope="files-vis-save"
            className="h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white">保存</button>
        </div>
      </div>
    </div>
  );
}

/** 配额将满预检弹层（C.115 / BR-14：≥95% 上传前「仍要上传」/「取消」）。 */
export function QuotaConfirmDialog({ used, quota, incoming, onProceed, onClose }: {
  used: number; quota: number; incoming: number; onProceed: () => void; onClose: () => void;
}) {
  const pct = quota > 0 ? Math.round(((used + incoming) / quota) * 100) : 100;
  return (
    <ConfirmDialog title="存储配额将满" width={440}
      body={<>工作区已用 <b className="font-mono">{(used / 1024 ** 3).toFixed(2)} / {(quota / 1024 ** 3).toFixed(0)} GB（{pct}%）</b>。<br />
        本次上传共 <b>{(() => { const kb = incoming / 1024; return kb >= 1024 ? `${(kb / 1024).toFixed(1)}MB` : `${Math.round(kb)}KB`; })()}</b>，完成后将达 {pct}%。</>}
      okText="仍要上传" onOk={onProceed} onClose={onClose} />
  );
}

/** toast 直达（弹层内提交后的轻提示）。 */
export { toast };
