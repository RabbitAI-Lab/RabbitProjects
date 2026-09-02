import { makeAutoObservable } from "mobx";
import type { Issue, WorkspaceSummary } from "@rp/types";
import { AuthAPI, type MeEnvelope } from "../services/api";

export class SessionStore {
  user: MeEnvelope["user"] | null = null;
  workspaces: WorkspaceSummary[] = [];
  currentWsSlug: string | null = null;
  isBootstrapped = false;
  justRegistered = false;

  constructor() {
    makeAutoObservable(this);
  }

  get isLoggedIn(): boolean {
    return this.user != null;
  }

  setSession(env: MeEnvelope) {
    this.user = env.user;
    this.workspaces = env.workspaces;
    this.currentWsSlug = env.default_workspace_slug;
  }

  setCurrentWs(slug: string) {
    this.currentWsSlug = slug;
  }

  async bootstrap(): Promise<boolean> {
    try {
      const r = await AuthAPI.me();
      this.setSession((r as any).data);
      this.isBootstrapped = true;
      return true;
    } catch {
      this.user = null;
      this.workspaces = [];
      this.currentWsSlug = null;
      this.isBootstrapped = true;
      return false;
    }
  }

  async signIn(email: string, password: string, remember = false): Promise<void> {
    const r = await AuthAPI.signIn(email, password, remember);
    this.setSession((r as any).data);
  }

  async signUp(email: string, password: string, displayName?: string, justRegistered?: boolean): Promise<void> {
    const r = await AuthAPI.signUp(email, password, displayName ?? (justRegistered ? undefined : undefined));
    this.setSession((r as any).data);
    if (justRegistered) this.justRegistered = true; // AUTH-001 §3.5：注册成功工作台顶部一次性欢迎条
  }

  async signOut(): Promise<void> {
    await AuthAPI.signOut();
    this.user = null;
    this.workspaces = [];
    this.currentWsSlug = null;
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
}

const Context = React.createContext<RootStore | null>(null);
export const StoreProvider = Context.Provider;
export function useStores(): RootStore {
  const v = React.useContext(Context);
  if (!v) throw new Error("useStores must be inside StoreProvider");
  return v;
}
