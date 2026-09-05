import { makeAutoObservable } from "mobx";
import type { Issue, WorkspaceSummary } from "@rp/types";
import { AuthAPI, type MeEnvelope } from "../services/api";
import { clearSessionProbe, markSessionProbe } from "../services/session-probe";
import { PermissionStore } from "./permission";

export class SessionStore {
  user: MeEnvelope["user"] | null = null;
  workspaces: WorkspaceSummary[] = [];
  currentWsSlug: string | null = null;
  isBootstrapped = false;
  justRegistered = false;
  /** AUTH-005 §2.6：登录态变化 → 联动权限快照 hydrate / reset。
   *  由 RootStore 在构造时反向注入，依赖关系避免循环 import。 */
  private _onLogin: (() => void) | null = null;
  private _onLogout: (() => void) | null = null;

  constructor() {
    makeAutoObservable(this);
  }

  /** RootStore 在构造完成后回调注入；保持 SessionStore 自身无 PermissionStore import。 */
  bindPermissionLifecycle(onLogin: () => void, onLogout: () => void) {
    this._onLogin = onLogin;
    this._onLogout = onLogout;
  }

  get isLoggedIn(): boolean {
    return this.user != null;
  }

  setSession(env: MeEnvelope) {
    this.user = env.user;
    this.workspaces = env.workspaces;
    this.currentWsSlug = env.default_workspace_slug;
    markSessionProbe();
  }

  setCurrentWs(slug: string) {
    this.currentWsSlug = slug;
  }

  /** 局部更新当前用户（AUTH-004：资料/头像保存后刷新顶栏，无需整包重拉） */
  setUser(displayName: string, avatarUrl: string | null) {
    if (this.user) {
      this.user = { ...this.user, display_name: displayName, avatar_url: avatarUrl };
    }
  }

  async bootstrap(): Promise<boolean> {
    try {
      const r = await AuthAPI.me();
      this.setSession((r as any).data);
      this.isBootstrapped = true;
      this._onLogin?.();                       // AUTH-005 §2.6：登录成功 → 拉权限快照
      return true;
    } catch {
      this.user = null;
      this.workspaces = [];
      this.currentWsSlug = null;
      this.isBootstrapped = true;
      clearSessionProbe();
      return false;
    }
  }

  async signIn(email: string, password: string, remember = false): Promise<void> {
    const r = await AuthAPI.signIn(email, password, remember);
    this.setSession((r as any).data);
    this._onLogin?.();                         // AUTH-005 §2.6：登录成功 → 拉权限快照
  }

  async signUp(email: string, password: string, displayName?: string, justRegistered?: boolean): Promise<void> {
    const r = await AuthAPI.signUp(email, password, displayName);
    this.setSession((r as any).data);
    if (justRegistered) this.justRegistered = true; // AUTH-001 §3.5：注册成功工作台顶部一次性欢迎条
    this._onLogin?.();                         // AUTH-005 §2.6：注册即登录 → 拉权限快照
  }

  async signOut(): Promise<void> {
    await AuthAPI.signOut();
    this.user = null;
    this.workspaces = [];
    this.currentWsSlug = null;
    clearSessionProbe();
    this._onLogout?.();                        // AUTH-005 §2.6：登出 → 清空权限快照
  }
}

export interface BoardColumn {
  stateId: string | null;
  stateName: string;
  issues: Issue[];
}

export class BoardStore {
  columns: BoardColumn[] = [];
  loading = false;

  constructor() {
    makeAutoObservable(this);
  }

  setColumns(c: BoardColumn[]) {
    this.columns = c;
  }
  setLoading(v: boolean) {
    this.loading = v;
  }
}

import React from "react";

export class RootStore {
  session = new SessionStore();
  board = new BoardStore();
  permission = new PermissionStore(this);

  constructor() {
    // AUTH-005 §2.6：登录 → hydrate；登出 → reset（避免 PermissionStore 反向 import SessionStore 造成循环）
    this.session.bindPermissionLifecycle(
      () => { this.permission.refetch().catch(() => { /* 拉取失败保留 fail-closed，由 PermissionStore 自身恢复 */ }); },
      () => { this.permission.reset(); },
    );
  }
}

const Context = React.createContext<RootStore | null>(null);
export const StoreProvider = Context.Provider;
export function useStores(): RootStore {
  const v = React.useContext(Context);
  if (!v) throw new Error("useStores must be inside StoreProvider");
  return v;
}
