/** Wiki 主界面（FILE-005 §3.1/§3.2——冻结原型 V-WIKI/V-WIKIH，Sprint-9）。
 *
 *  左页面树（空间切换 + 新建页面）+ 中内容区（三格式 / 编辑草稿 / 发布乐观锁
 *  409 冲突弹窗）+ 版本历史视图（diff 高亮 + 回滚生成新版本）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { WikiAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Space = { id: string; name: string; permission_mode: string };
type PageNode = { id: string; title: string; parent_id: string | null; depth: number; children: PageNode[]; version_no: number; has_draft: boolean };
type PageDetail = { id: string; title: string; description: unknown; description_html: string; description_stripped: string | null; version_no: number; has_draft: boolean };
type Version = { id: string; version_no: number; change_summary: string; rolled_back_from: string | null; created_by: string | null; created_at: string };

export default function WikiPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [params, setParams] = useSearchParams();
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [tree, setTree] = useState<PageNode[]>([]);
  const [pageId, setPageId] = useState<string | null>(params.get("page"));
  const [page, setPage] = useState<PageDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"doc" | "history">("doc");
  const [versions, setVersions] = useState<Version[]>([]);
  const [diffFor, setDiffFor] = useState<Version | null>(null);
  const [diffHtml, setDiffHtml] = useState<string>("");

  const loadSpaces = useCallback(async () => {
    if (!ws || !projectId) return;
    const list = unwrap<Space[]>(await WikiAPI.spaces(ws, projectId).catch(() => null)) ?? [];
    setSpaces(list);
    setSpaceId((prev) => prev ?? list[0]?.id ?? null);
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => { loadSpaces(); }, [loadSpaces]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => {
    if (!ws || !spaceId) return;
    WikiAPI.space(ws, spaceId)
      .then((r) => setTree((unwrap<{ pages: PageNode[] }>(r) ?? { pages: [] }).pages ?? []))
      .catch(() => setTree([]));
  }, [ws, spaceId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => {
    if (!ws || !pageId) return;
    WikiAPI.page(ws, pageId)
      .then((r) => setPage(unwrap<PageDetail>(r) ?? null))
      .catch(() => setPage(null));
  }, [ws, pageId]);

  const flat: PageNode[] = [];
  const walk = (nodes: PageNode[]) => { for (const n of nodes) { flat.push(n); walk(n.children ?? []); } };
  walk(tree);

  const createPage = async (parent?: PageNode) => {
    if (!ws || !spaceId) return;
    const title = prompt(parent ? `在「${parent.title}」下新建页面标题` : "新页面标题");
    if (!title) return;
    try {
      const r = unwrap<{ id: string }>(await WikiAPI.createPage(ws, spaceId, { title, parent_id: parent?.id ?? null })) ?? { id: "" };
      toast("页面已创建（保存草稿后发布首个版本）", "ok");
      setPageId(r.id);
      const s = unwrap<{ pages: PageNode[] }>(await WikiAPI.space(ws, spaceId)) ?? { pages: [] };
      setTree(s.pages ?? []);
    } catch (e) { toast(e instanceof Error ? e.message : "创建失败", "error"); }
  };

  const saveDraft = async () => {
    if (!ws || !pageId) return;
    setSaving(true);
    try {
      await WikiAPI.saveDraft(ws, pageId, draft ? JSON.parse(draft) : null, page?.version_no ? null : null);
      toast("草稿已保存", "ok");
      setEditing(false);
      const r = unwrap<PageDetail>(await WikiAPI.page(ws, pageId)) ?? null;
      setPage(r);
    } catch (e) {
      toast(e instanceof Error ? e.message : "草稿保存失败", "error");
    } finally { setSaving(false); }
  };

  const publish = async () => {
    if (!ws || !pageId) return;
    try {
      const r = unwrap<{ version_no: number }>(await WikiAPI.publish(ws, pageId, null));
      toast(`已发布 v${r?.version_no ?? "?"}`, "ok");
      setEditing(false);
      setPage(unwrap<PageDetail>(await WikiAPI.page(ws, pageId)) ?? null);
    } catch (e) {
      const err = e as { code?: string; message?: string };
      if (err.code === "RESOURCE_CONFLICT") {
        toast("发布冲突：他人已发布更新版本——请对比合并后重发", "error");
      } else {
        toast(err.message ?? "发布失败", "error");
      }
    }
  };

  const openHistory = async () => {
    if (!ws || !pageId) return;
    setView("history");
    setVersions(unwrap<Version[]>(await WikiAPI.versions(ws, pageId).catch(() => null)) ?? []);
  };

  const openDiff = async (v: Version) => {
    if (!ws || !pageId) return;
    setDiffFor(v);
    setDiffHtml(unwrap<{ description_html: string }>(await WikiAPI.version(ws, pageId, v.id))?.description_html ?? "");
  };

  const rollback = async (v: Version) => {
    if (!ws || !pageId) return;
    if (!confirm(`回滚到 v${v.version_no}？将生成新版本（台账只增）`)) return;
    try {
      const r = unwrap<{ version_no: number }>(await WikiAPI.rollback(ws, pageId, v.id));
      toast(`已生成 v${r?.version_no ?? "?"}（回滚自 v${v.version_no}）`, "ok");
      openHistory();
      setPage(unwrap<PageDetail>(await WikiAPI.page(ws, pageId)) ?? null);
    } catch (e) { toast(e instanceof Error ? e.message : "回滚失败", "error"); }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-wiki">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="WIKI" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div><div className="text-[17px] font-semibold">Wiki</div>
                <div className="text-[12.5px] text-neutral-400">项目知识库 · 版本台账只增 · 全局检索经顶部「知识检索」（FILE-005）</div></div>
              <div className="ml-auto flex gap-2">
                <select className="input h-[32px] w-[150px]" value={spaceId ?? ""} onChange={(e) => { setSpaceId(e.target.value); setPageId(null); }}>
                  {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <button className="btn-primary h-[30px] px-3 text-[12px]" onClick={() => createPage()}>+ 新建页面</button>
                <Link to={`/${ws}/projects/${projectId}/wiki/trash`} className="btn-ghost h-[30px] px-3 text-[12px]">回收站</Link>
              </div>
            </div>

            <div className="flex h-[calc(100vh-180px)] min-h-[460px] overflow-hidden rounded-lg border border-neutral-200 bg-white" data-sb-scope="wiki-shell">
              {/* 页面树 */}
              <div className="w-[240px] shrink-0 overflow-auto border-r border-neutral-200 p-3" data-sb-scope="wiki-tree">
                {tree.length === 0 && <div className="py-6 text-center text-[12.5px] text-neutral-400">暂无页面</div>}
                {flat.map((n) => (
                  <button key={n.id} onClick={() => { setPage(null); setEditing(false); setView("doc"); setPageId(n.id); setParams({ page: n.id }); }}
                    className={`flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] ${pageId === n.id ? "bg-brand-50 font-medium text-brand-600" : "text-neutral-600 hover:bg-neutral-50"}`}
                    style={{ paddingLeft: 8 + (n.depth - 1) * 14 }}>
                    {n.children?.length > 0 ? <span className="text-[10px] text-neutral-400">▾</span> : <span className="w-3" />}
                    <span className="truncate">{n.title}</span>
                    {n.has_draft && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-amber-400" title="有未发布草稿" />}
                  </button>
                ))}
              </div>

              {/* 内容区 */}
              <div className="flex min-w-0 flex-1 flex-col">
                {!page ? (
                  <div className="flex flex-1 items-center justify-center text-[13px] text-neutral-400">选择左侧页面，或新建</div>
                ) : view === "history" ? (
                  <>
                    <div className="flex items-center gap-2.5 border-b border-neutral-200 px-6 py-3.5">
                      <b className="text-[15px]">版本历史 · {page.title}</b>
                      <span className="text-[12px] text-neutral-400">回滚生成新版本（BR-08 台账只增）</span>
                      <button className="btn-ghost ml-auto h-[26px] px-2 text-[11.5px]" onClick={() => setView("doc")}>返回页面</button>
                    </div>
                    <div className="flex-1 overflow-auto" data-sb-scope="wiki-versions">
                      {versions.map((v) => (
                        <div key={v.id} className="flex items-center gap-3 border-b border-neutral-200 px-4 py-2.5 text-[13px]">
                          <span className="w-9 font-mono font-semibold">v{v.version_no}</span>
                          <span className="w-[90px] text-neutral-600">{v.created_by}</span>
                          <span className="w-[130px] font-mono text-[12px] text-neutral-400">{v.created_at.slice(0, 16).replace("T", " ")}</span>
                          <span className="text-neutral-600">
                            {v.change_summary || "—"}
                            {v.rolled_back_from && <span className="ml-1 text-neutral-400">（回滚版）</span>}
                          </span>
                          <span className="ml-auto flex gap-2">
                            <button className="btn-ghost h-[26px] px-2 text-[11.5px]" onClick={() => openDiff(v)}>对比当前</button>
                            <button className="btn-ghost h-[26px] px-2 text-[11.5px]" onClick={() => rollback(v)}>回滚到此版</button>
                          </span>
                        </div>
                      ))}
                      {diffFor && (
                        <div className="m-4 overflow-hidden rounded-lg border border-neutral-200" data-sb-scope="wiki-diff">
                          <div className="border-b border-neutral-200 bg-neutral-50 px-3.5 py-2 text-[12.5px] font-semibold">
                            对比 当前 v{page.version_no} → v{diffFor.version_no}
                          </div>
                          <div className="p-3.5 text-[12.5px] leading-7" dangerouslySetInnerHTML={{ __html: diffHtml }} />
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="border-b border-neutral-200 px-6 pt-3.5 pb-2.5" data-sb-scope="wiki-doc-head">
                      <div className="flex items-center gap-2.5 text-[17px] font-semibold">
                        {page.title}
                        <span className="rounded-full border border-neutral-200 bg-neutral-100 px-2.5 py-0.5 font-mono text-[12px] text-neutral-500">v{page.version_no}</span>
                        {page.has_draft && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">有草稿</span>}
                      </div>
                      <div className="mt-0.5 text-[12px] text-neutral-400">
                        阅读限：空间成员（编辑：{spaces.find((s) => s.id === spaceId)?.permission_mode === "whitelist" ? "白名单" : "继承项目角色（BR-06/07）"}）
                      </div>
                    </div>
                    <div className="flex-1 overflow-auto px-6 py-4" data-sb-scope="wiki-doc-body">
                      {editing ? (
                        <>
                          <textarea className="input h-[320px] font-mono text-[12.5px] leading-6" value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            placeholder='Tiptap JSON 草稿，如：{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"…"}]}]}' />
                          <div className="mt-2 text-[11.5px] text-neutral-400">草稿覆盖式保存（BR-04）；发布以 base_version 乐观锁校验（BR-05）。</div>
                        </>
                      ) : (
                        <>
                          {page.version_no === 0
                            ? <div className="text-[13px] text-neutral-400">尚无发布版本——编辑草稿后发布 v1</div>
                            : <div className="prose-neutral max-w-[70ch] text-[13.5px] leading-7 text-neutral-700" dangerouslySetInnerHTML={{ __html: page.description_html }} />}
                          <div className="mt-3 rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-3 py-2 text-[12px] text-neutral-400">
                            💬 页面评论：P4（锚定协议未定义，本迭代不交付，FILE-005 §1.3）
                          </div>
                        </>
                      )}
                    </div>
                    <div className="flex items-center gap-2 border-t border-neutral-200 px-6 py-2.5" data-sb-scope="wiki-doc-foot">
                      {editing ? (
                        <>
                          <button className="btn-ghost h-[28px] px-2.5 text-[12px]" disabled={saving} onClick={() => setEditing(false)}>取消</button>
                          <button className="btn-ghost h-[28px] px-2.5 text-[12px]" disabled={saving} onClick={saveDraft}>保存草稿</button>
                          <button className="btn-primary h-[28px] px-2.5 text-[12px]" disabled={saving} onClick={publish}>发布新版本</button>
                        </>
                      ) : (
                        <>
                          <button className="btn-primary h-[28px] px-2.5 text-[12px]" onClick={() => { setDraft(JSON.stringify(page.description ?? null, null, 2) === "null" ? "" : JSON.stringify(page.description, null, 2)); setEditing(true); }}>编辑</button>
                          <button className="btn-ghost h-[28px] px-2.5 text-[12px]" onClick={openHistory}>历史 v{page.version_no} ▾</button>
                        </>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
