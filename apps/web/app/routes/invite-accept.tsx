/** 邀请接受页（C.17 · TEAM-002 §3.4）——独立轻路由，不进工作空间布局。
 *  三态：有效（工作区名 + 邀请人 + 脱敏邮箱 + 接受按钮）
 *        邮箱不匹配（email_match=false → 黄条提示当前账号与邀请邮箱不一致）
 *        失效/过期（token 已用/撤销/过期 → 统一「邀请无效」+ 联系管理员）
 *  未登录：precheck 401 由 axios 拦截器跳 /login?next=/invite/:token（C.17 未登录路径）。
 *  接受成功 → 跳该工作空间项目列表（服务端保证成员行已建）。 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { Logo } from "../components/Logo";
import { toast } from "../components/Toast";
import { InvitationAPI, unwrap } from "../services/api";
import { WorkspaceRole } from "../stores/permission";
import { useStores } from "../stores";

type Precheck = {
  workspace: { id: string; name: string; slug: string };
  role: number;
  invited_by: { id: string; display_name: string; email: string } | null;
  expires_at: string;
  masked_email: string;
  email_match: boolean;
};

const ROLE_NAMES: Record<number, string> = {
  [WorkspaceRole.OWNER]: "所有者",
  [WorkspaceRole.ADMIN]: "管理员",
  [WorkspaceRole.MEMBER]: "成员",
  [WorkspaceRole.GUEST]: "访客",
};

export default function InviteAcceptPage() {
  const { token = "" } = useParams<{ token: string }>();
  const nav = useNavigate();
  const { session } = useStores();
  const [state, setState] = useState<"loading" | "valid" | "mismatch" | "invalid">("loading");
  const [info, setInfo] = useState<Precheck | null>(null);
  const [errMsg, setErrMsg] = useState("");
  const [accepting, setAccepting] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => {
      void (async () => {
        try {
          const r = await InvitationAPI.precheck(token);
          const d = unwrap<Precheck>(r);
          setInfo(d);
          setState(d.email_match ? "valid" : "mismatch");
        } catch (e) {
          setErrMsg(e instanceof Error ? e.message : "邀请无效");
          setState("invalid");
        }
      })();
    }, 0);
    return () => clearTimeout(id);
  }, [token]);

  async function accept() {
    setAccepting(true);
    try {
      const r = await InvitationAPI.accept(token);
      const d = unwrap<{ workspace: { slug: string } }>(r);
      await session.bootstrap();
      session.setCurrentWs(d.workspace.slug);
      toast("已加入团队");
      location.href = `/${d.workspace.slug}/projects`;
    } catch (e) {
      toast(e instanceof Error ? e.message : "接受失败", "error");
      setAccepting(false);
    }
  }

  return (
    <div className="min-h-screen bg-neutral-50 flex flex-col items-center pt-[14vh] px-4">
      <div className="flex items-center gap-2 text-neutral-800 mb-6">
        <Logo />
        <span className="text-[15px] font-semibold">RabbitProjects</span>
      </div>

      <div className="w-full max-w-[420px] bg-white border border-neutral-200 rounded-xl shadow-sm p-6">
        {state === "loading" && (
          <div className="py-8 space-y-3" aria-busy="true">
            <div className="h-5 w-2/3 rounded bg-neutral-100 animate-pulse" />
            <div className="h-4 w-full rounded bg-neutral-100 animate-pulse" />
            <div className="h-9 w-full rounded bg-neutral-100 animate-pulse" />
          </div>
        )}

        {state === "invalid" && (
          <div className="text-center py-4">
            <svg className="mx-auto text-neutral-400" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              <circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" />
            </svg>
            <h1 className="mt-3 text-[16px] font-semibold text-neutral-900">邀请无效</h1>
            <p className="mt-1.5 text-[13px] text-neutral-500">{errMsg}。该链接可能已被使用、撤销或已过期。</p>
            <p className="mt-1 text-[12px] text-neutral-400">认为有问题？请联系空间管理员重新发送邀请。</p>
          </div>
        )}

        {(state === "valid" || state === "mismatch") && info && (
          <>
            <h1 className="text-[16px] font-semibold text-neutral-900">加入团队邀请</h1>
            <p className="mt-1.5 text-[13px] text-neutral-600">
              {info.invited_by ? `${info.invited_by.display_name} ` : ""}
              邀请你以「{ROLE_NAMES[info.role] ?? "成员"}」身份加入
            </p>
            <div className="mt-4 border border-neutral-200 rounded-lg px-3.5 py-3 bg-neutral-50">
              <div className="text-[15px] font-medium text-neutral-900">{info.workspace.name}</div>
              <div className="text-[12px] text-neutral-400 mt-0.5">rabbit.example.com/{info.workspace.slug}</div>
            </div>
            <p className="mt-3 text-[12px] text-neutral-400">邀请邮箱：{info.masked_email}</p>

            {state === "mismatch" && (
              <div className="mt-3 flex items-start gap-2 bg-amber-50 border border-amber-200 text-amber-700 rounded-md px-3 py-2 text-[12px]" role="alert">
                <svg className="mt-0.5 shrink-0" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>
                当前登录账号与邀请邮箱不一致，可能无法完成接受。请用被邀请的邮箱登录后再打开此链接。
              </div>
            )}

            <button
              className="mt-5 w-full h-10 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white rounded-md text-[14px] font-medium"
              disabled={accepting}
              onClick={accept}
            >
              {accepting ? "正在加入…" : "接受邀请"}
            </button>
            <button
              className="mt-2 w-full h-10 border border-neutral-300 bg-white hover:bg-neutral-50 text-neutral-700 rounded-md text-[14px]"
              onClick={() => nav("/login")}
            >
              换个账号登录
            </button>
          </>
        )}
      </div>
    </div>
  );
}
