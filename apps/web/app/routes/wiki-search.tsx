/** 全局知识检索（FILE-005 §3.3——冻结原型 V-SEARCH，Sprint-9）。
 *
 *  Wiki 专属独立入口：标题 3x 权重 + 正文 trgm/包含 + ts_headline 片段高亮；
 *  权限过滤 SQL 前置（BR-10），不并入 ⌘K 全局搜索（口径见 §3.3 说明）。 */
import { useState } from "react";
import { useParams } from "react-router";

import { WikiAPI, unwrap } from "../services/api";
import { Sidebar } from "../components/Sidebar";
import { Topbar } from "../components/Topbar";

type Hit = { page_id: string; title: string; space_name: string; identifier: string; snippet: string; rank: number };

export default function WikiSearchPage() {
  const { workspaceSlug: ws } = useParams();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [busy, setBusy] = useState(false);

  const search = async () => {
    if (!ws || !q.trim()) { setHits(null); return; }
    setBusy(true);
    try {
      setHits(unwrap<Hit[]>(await WikiAPI.search(ws, q.trim())) ?? []);
    } catch { setHits([]); } finally { setBusy(false); }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-wiki-search">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[900px] px-2 py-4">
            <div className="mb-[18px]">
              <div className="text-[17px] font-semibold">知识检索</div>
              <div className="text-[12.5px] text-neutral-400">仅 Wiki 页面命中（独立入口 · 不与 ⌘K 全局搜索混排，FILE-005 §3.3）</div>
            </div>
            <div className="mb-4 flex gap-2.5" data-sb-scope="wiki-search-bar">
              <input className="input max-w-[380px]" value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && search()} placeholder='如：错误码' aria-label="搜索" />
              <button className="btn-primary" onClick={search} disabled={busy || !q.trim()}>🔍 搜索</button>
              {hits && <span className="self-center text-[12px] text-neutral-400">{hits.length} 条结果 · 标题命中 3x 权重置顶</span>}
            </div>
            <div className="card" data-sb-scope="wiki-search-results">
              {hits === null && <div className="p-10 text-center text-[12.5px] text-neutral-400">输入关键词检索标题与正文（片段 ≤160 字）</div>}
              {hits?.length === 0 && <div className="p-10 text-center text-[12.5px] text-neutral-400">无命中——换更短的关键词或检查拼写</div>}
              {hits?.map((h) => (
                <div key={h.page_id} className="border-b border-neutral-200 px-4 py-3 last:border-b-0">
                  <div className="text-[13.5px] font-semibold">📄 {h.title}</div>
                  <div className="mt-0.5 text-[12px] text-neutral-400">{h.space_name} · {h.identifier} · 相关度 {h.rank.toFixed(3)}</div>
                  <div className="mt-1 text-[13px] text-neutral-600">{h.snippet}</div>
                </div>
              ))}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
