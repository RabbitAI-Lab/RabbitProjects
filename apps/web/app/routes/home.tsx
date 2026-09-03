import { useEffect } from "react";
import { useNavigate } from "react-router";
import { AuthAPI, type MeEnvelope } from "../services/api";
import { useStores } from "../stores";

export default function Home() {
  const { session } = useStores();
  const nav = useNavigate();
  useEffect(() => {
    AuthAPI.me()
      .then((r) => {
        const env = (r as any).data as MeEnvelope;
        session.setSession(env);
        nav(`/${env.default_workspace_slug ?? "login"}/projects`);
      })
      .catch(() => nav("/login"));
  }, []);
  return <div className="flex h-screen items-center justify-center text-sm text-neutral-500">加载中…</div>;
}
