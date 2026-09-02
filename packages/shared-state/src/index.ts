import { action, computed, makeObservable, observable } from "mobx";
import type { Issue, ProjectSummary, UUID, WorkspaceSummary } from "@rp/types";

/** 本包不发起 HTTP 请求（monorepo-structure.md §4）：数据由 app 层 services/ 注入。 */

export class SessionStore {
  currentUser: { name: string; email: string } | null = null;
  isBootstrapped = false;

  constructor() {
    makeObservable(this, {
      currentUser: observable,
      isBootstrapped: observable,
      setUser: action,
    });
  }

  setUser(user: { name: string; email: string } | null): void {
    this.currentUser = user;
    this.isBootstrapped = true;
  }
}

export class WorkspaceStore {
  list: WorkspaceSummary[] = [];
  currentSlug: string | null = null;
  projects: ProjectSummary[] = [];

  constructor() {
    makeObservable(this, {
      list: observable,
      currentSlug: observable,
      projects: observable,
      current: computed,
      hydrate: action,
      switchWorkspace: action,
      setProjects: action,
    });
  }

  get current(): WorkspaceSummary | undefined {
    return this.list.find((w) => w.slug === this.currentSlug) ?? this.list[0];
  }

  hydrate(list: WorkspaceSummary[], slug: string | null): void {
    this.list = list;
    this.currentSlug = slug;
  }

  switchWorkspace(slug: string): void {
    this.currentSlug = slug;
    this.projects = [];
  }

  setProjects(projects: ProjectSummary[]): void {
    this.projects = projects;
  }
}

/** 看板视图状态：分组渲染与排序求值可脱离 UI 单测（monorepo-structure.md 改进点 2）。 */
export class BoardStore {
  issuesByColumn = new Map<string, Issue[]>();

  constructor() {
    makeObservable(this, {
      issuesByColumn: observable.shallow,
      setColumn: action,
    });
  }

  setColumn(stateGroup: string, issues: Issue[]): void {
    this.issuesByColumn.set(stateGroup, issues);
  }

  columnIssues(stateGroup: string): Issue[] {
    return this.issuesByColumn.get(stateGroup) ?? [];
  }
}

export interface RootStore {
  session: SessionStore;
  workspace: WorkspaceStore;
  board: BoardStore;
}

export function createRootStore(): RootStore {
  return { session: new SessionStore(), workspace: new WorkspaceStore(), board: new BoardStore() };
}

export type { Issue, ProjectSummary, UUID };
