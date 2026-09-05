/** 个人资料页（C.10 · AUTH-004 §3.2 / §3.5–§3.7）。
 *  头像卡（160px 圆形 + hover 遮罩「更换」+ 恢复默认[条件态] + 直传进度环 + onError 回退）
 *  资料表单（昵称* / 名 / 姓 / 简介 3 行 + 字数统计 超 480 琥珀）
 *  失焦校验、全表单显式保存（⌘S）、成功「✓ 已保存」2s、重置按钮、失败回滚
 *  邮箱只读 + 锁 + Tooltip「邮箱变更即将上线」；/users/me/ 失败 → 重试态。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { SettingsShell } from "../components/SettingsShell";
import { toast } from "../components/Toast";
import { AuthAPI, ProfileAPI, unwrap, type Profile } from "../services/api";
import { useStores } from "../stores";

const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"];
const AVATAR_MAX = 5 * 1024 * 1024; // 5MB
const INTRO_MAX = 500;

export default function SettingsProfilePage() {
  const { session } = useStores();
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");
  const [me, setMe] = useState<Profile | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [intro, setIntro] = useState("");
  const [nameErr, setNameErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  const [avatarBroken, setAvatarBroken] = useState(false);
  const [upPct, setUpPct] = useState<number | null>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await AuthAPI.me();
      const env = unwrap<{ user: Record<string, unknown> }>(r);
      const u = env.user as unknown as Profile;
      setMe(u);
      setDisplayName(u.display_name ?? "");
      setFirstName(u.first_name ?? "");
      setLastName(u.last_name ?? "");
      setIntro(u.intro ?? "");
      setLoadState("ready");
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : "加载失败");
      setLoadState("error");
    }
  }, []);

  useEffect(() => {
    const id = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(id);
  }, [load]);

  const dirty =
    !!me &&
    (displayName !== (me.display_name ?? "") ||
      firstName !== (me.first_name ?? "") ||
      lastName !== (me.last_name ?? "") ||
      intro !== (me.intro ?? ""));

  function reset() {
    if (!me) return;
    setDisplayName(me.display_name ?? "");
    setFirstName(me.first_name ?? "");
    setLastName(me.last_name ?? "");
    setIntro(me.intro ?? "");
    setNameErr("");
  }

  const save = useCallback(async () => {
    if (!dirty || saving) return;
    const name = displayName.trim();
    if (!name) { setNameErr("昵称为必填项"); return; }
    setSaving(true);
    try {
      const r = await ProfileAPI.patch({
        display_name: name,
        first_name: firstName,
        last_name: lastName,
        intro,
      });
      const p = unwrap<Profile>(r);
      setMe(p);
      session.setUser(p.display_name, p.avatar_url);
      setSavedFlash(true);
      toast("资料已保存");
      setTimeout(() => setSavedFlash(false), 2000); // C.10：「✓ 已保存」持续 2s
    } catch (e) {
      // 失败：toast 提示（表单值保留供修正，§3.6）
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }, [dirty, saving, displayName, firstName, lastName, intro, session]);

  // ⌘S 提交（§3.6）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  function pickAvatar() { fileRef.current?.click(); }

  async function onFile(f: File) {
    // 前端预检：MIME + 大小，非法即 Toast、不发 presign（§3.5）
    if (!AVATAR_TYPES.includes(f.type)) { toast("仅支持 PNG / JPEG / WebP 图片", "error"); return; }
    if (f.size > AVATAR_MAX) { toast("头像不能超过 5MB", "error"); return; }
    try {
      const pre = unwrap(await ProfileAPI.avatarPresign({
        file_name: f.name, file_size: f.size, content_type: f.type,
      })) as { asset_id: string; upload_url: string };
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhrRef.current = xhr;
        xhr.open("PUT", pre.upload_url);
        xhr.setRequestHeader("Content-Type", f.type);
        xhr.upload.onprogress = (ev) => {
          if (ev.lengthComputable) setUpPct(Math.round((ev.loaded / ev.total) * 100));
        };
        xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`上传失败 (${xhr.status})`)));
        xhr.onerror = () => reject(new Error("上传失败"));
        xhr.onabort = () => reject(new Error("已取消"));
        xhr.send(f);
      });
      const done = unwrap(await ProfileAPI.avatarComplete({ asset_id: pre.asset_id })) as { avatar_url: string };
      setMe((cur) => (cur ? { ...cur, avatar_url: done.avatar_url, is_default_avatar: false } : cur));
      session.setUser(me?.display_name ?? "", done.avatar_url);
      setAvatarBroken(false);
      toast("头像已更新");
    } catch (e) {
      toast(e instanceof Error ? e.message : "头像上传失败", "error");
    } finally {
      setUpPct(null);
      xhrRef.current = null;
    }
  }

  async function restoreDefaultAvatar() {
    if (!confirm("恢复为系统默认头像？")) return; // 二次确认（§3.2）
    try {
      await ProfileAPI.avatarDelete();
      setMe((cur) => (cur ? { ...cur, avatar_url: null, is_default_avatar: true } : cur));
      setAvatarBroken(false);
      session.setUser(me?.display_name ?? "", null);
      toast("已恢复默认头像");
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败", "error");
    }
  }

  if (loadState === "loading") {
    return (
      <SettingsShell active="profile">
        <div className="space-y-4" aria-busy="true">
          <div className="h-[160px] w-[160px] rounded-full bg-neutral-100 animate-pulse" />
          <div className="h-9 rounded bg-neutral-100 animate-pulse" />
          <div className="h-9 rounded bg-neutral-100 animate-pulse" />
        </div>
      </SettingsShell>
    );
  }

  if (loadState === "error" || !me) {
    return (
      <SettingsShell active="profile">
        <div className="flex flex-col items-center gap-3 py-16 text-neutral-500">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
          <p className="text-[13px]">{errMsg}</p>
          <button className="h-8 px-3 border border-neutral-300 rounded-md text-[13px] bg-white hover:bg-neutral-50"
                  onClick={() => { setLoadState("loading"); void load(); }}>重试</button>
        </div>
      </SettingsShell>
    );
  }

  const showUploadRing = upPct !== null;

  return (
    <SettingsShell active="profile">
      {/* 头像卡（C.10） */}
      <div className="flex items-center gap-6">
        <div className="relative w-[160px] h-[160px] group">
          {me.avatar_url && !avatarBroken ? (
            <img
              src={me.avatar_url}
              alt="头像"
              className="w-full h-full rounded-full object-cover"
              onError={() => setAvatarBroken(true)} // 回退默认；avatar_url 不回写（§3.7）
            />
          ) : (
            <div className="w-full h-full rounded-full bg-gradient-to-br from-brand-100 to-brand-500 text-white flex items-center justify-center text-[52px] font-semibold">
              {me.display_name.slice(0, 1)}
            </div>
          )}
          {!showUploadRing && (
            <button
              className="absolute inset-0 rounded-full bg-black/50 text-white opacity-0 group-hover:opacity-100 transition flex flex-col items-center justify-center gap-1 text-[13px]"
              onClick={pickAvatar}
              aria-label="更换头像"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z"/><circle cx="12" cy="13" r="3.5"/></svg>
              更换
            </button>
          )}
          {showUploadRing && (
            <div className="absolute inset-0 rounded-full bg-white/85 flex flex-col items-center justify-center gap-1"
                 role="progressbar" aria-valuetext={`上传进度 ${upPct}%`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={upPct ?? 0}>
              <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
                <circle cx="32" cy="32" r="26" stroke="#e5e5e5" strokeWidth="6" fill="none" />
                <circle cx="32" cy="32" r="26" stroke="#3f76ff" strokeWidth="6" fill="none"
                        strokeDasharray={`${(upPct / 100) * 163.4} 163.4`} transform="rotate(-90 32 32)" strokeLinecap="round" />
              </svg>
              <span className="text-[12px] text-neutral-600">{upPct}%</span>
              <button className="text-[12px] text-neutral-400 hover:text-neutral-600"
                      onClick={() => xhrRef.current?.abort()}>取消</button>
            </div>
          )}
        </div>
        <div>
          <button className="h-8 px-3 border border-neutral-300 rounded-md text-[13px] bg-white hover:bg-neutral-50"
                  onClick={pickAvatar}>更换头像</button>
          {/* 恢复默认：仅 avatar_url 非空时显示（条件态） */}
          {!me.is_default_avatar && (
            <button className="ml-2 h-8 px-3 text-[13px] text-neutral-500 hover:text-neutral-800"
                    onClick={restoreDefaultAvatar}>恢复默认</button>
          )}
          <p className="mt-2 text-[12px] text-neutral-400">支持 PNG / JPEG / WebP，≤ 5MB</p>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f); e.target.value = ""; }} />
        </div>
      </div>

      {/* 资料表单（C.10）：失焦校验 + 显式保存 */}
      <div className="mt-8 space-y-4">
        <div>
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="pf-name">昵称 *</label>
          <input id="pf-name" className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] bg-white"
                 value={displayName}
                 onChange={(e) => { setDisplayName(e.target.value); if (nameErr) setNameErr(""); }}
                 onBlur={() => { if (!displayName.trim()) setNameErr("昵称为必填项"); }}
                 aria-invalid={!!nameErr} aria-describedby={nameErr ? "pf-name-err" : undefined} />
          {nameErr && <p id="pf-name-err" className="mt-1 text-[12px] text-red-600">{nameErr}</p>}
        </div>
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="pf-first">名</label>
            <input id="pf-first" className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] bg-white"
                   value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </div>
          <div className="flex-1">
            <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="pf-last">姓</label>
            <input id="pf-last" className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] bg-white"
                   value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="pf-intro">个人简介</label>
          <textarea id="pf-intro" rows={3} maxLength={INTRO_MAX}
                    className="w-full px-3 py-2 border border-neutral-300 rounded-md text-[13px] bg-white resize-none"
                    value={intro} onChange={(e) => setIntro(e.target.value)} />
          <p className={`mt-1 text-right text-[12px] ${intro.length > 480 ? "text-amber-600" : "text-neutral-400"}`}>
            {intro.length} / {INTRO_MAX}
          </p>
        </div>
        <div>
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">邮箱</label>
          <div className="relative">
            <input readOnly value={me.email} aria-readonly
                   className="w-full h-9 pl-3 pr-9 border border-neutral-200 rounded-md text-[13px] bg-neutral-50 text-neutral-500 cursor-not-allowed" />
            <span className="absolute right-2.5 top-2 text-neutral-400" title="邮箱变更即将上线">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            className="h-9 px-4 bg-brand-500 hover:bg-brand-600 text-white rounded-md text-[13px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={!dirty || saving}
            onClick={() => void save()}
          >
            {saving ? "保存中…" : savedFlash ? "✓ 已保存" : "保存"}
          </button>
          <button className="h-9 px-4 border border-neutral-300 rounded-md text-[13px] bg-white hover:bg-neutral-50 disabled:opacity-40"
                  disabled={!dirty && !nameErr} onClick={reset}>重置</button>
        </div>
      </div>
    </SettingsShell>
  );
}
