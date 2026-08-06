import { users, projects, projectGroups, accessRules, appSettings, type User, type InsertUser, type Project, type InsertProject, type ProjectGroup, type InsertProjectGroup, type AccessRule, type InsertAccessRule } from "@shared/schema";
import { db } from "./db";
import { eq, and, or, inArray } from "drizzle-orm";

export interface IStorage {
  // Users
  getUser(id: number): User | undefined;
  getUserByUsername(username: string): User | undefined;
  getUsers(): User[];
  createUser(data: InsertUser): User;
  updateUser(id: number, data: Partial<InsertUser>): User | undefined;
  deleteUser(id: number): void;

  // Projects
  getProject(id: number): Project | undefined;
  getProjects(): Project[];
  getProjectsByGroup(groupId: number): Project[];
  createProject(data: InsertProject): Project;
  updateProject(id: number, data: Partial<InsertProject>): Project | undefined;
  deleteProject(id: number): void;

  // Project Groups
  getProjectGroup(id: number): ProjectGroup | undefined;
  getProjectGroups(): ProjectGroup[];
  createProjectGroup(data: InsertProjectGroup): ProjectGroup;
  updateProjectGroup(id: number, data: Partial<InsertProjectGroup>): ProjectGroup | undefined;
  deleteProjectGroup(id: number): void;

  // Access rules
  getAccessRules(): AccessRule[];
  getAccessRulesForUser(userId: number): AccessRule[];
  createAccessRule(data: InsertAccessRule): AccessRule;
  updateAccessRule(id: number, data: Partial<InsertAccessRule>): AccessRule | undefined;
  deleteAccessRule(id: number): void;

  // Convenience
  getUserAccessibleProjectIds(userId: number): number[];
  getUserAccessibleProjectMap(userId: number): Map<number, string>;

  // App Settings
  getSetting(key: string): string | undefined;
  setSetting(key: string, value: string): void;
}

export class DatabaseStorage implements IStorage {
  getUser(id: number): User | undefined {
    return db.select().from(users).where(eq(users.id, id)).get();
  }

  getUserByUsername(username: string): User | undefined {
    return db.select().from(users).where(eq(users.username, username)).get();
  }

  getUsers(): User[] {
    return db.select().from(users).all();
  }

  createUser(data: InsertUser): User {
    return db.insert(users).values({ ...data, createdAt: new Date().toISOString() }).returning().get();
  }

  updateUser(id: number, data: Partial<InsertUser>): User | undefined {
    return db.update(users).set(data).where(eq(users.id, id)).returning().get();
  }

  deleteUser(id: number): void {
    db.delete(users).where(eq(users.id, id)).run();
    db.delete(accessRules).where(eq(accessRules.userId, id)).run();
  }

  getProject(id: number): Project | undefined {
    return db.select().from(projects).where(eq(projects.id, id)).get();
  }

  getProjects(): Project[] {
    return db.select().from(projects).all();
  }

  getProjectsByGroup(groupId: number): Project[] {
    return db.select().from(projects).where(eq(projects.groupId, groupId)).all();
  }

  createProject(data: InsertProject): Project {
    const now = new Date().toISOString();
    return db.insert(projects).values({ ...data, createdAt: now, updatedAt: now }).returning().get();
  }

  updateProject(id: number, data: Partial<InsertProject>): Project | undefined {
    return db.update(projects).set({ ...data, updatedAt: new Date().toISOString() }).where(eq(projects.id, id)).returning().get();
  }

  deleteProject(id: number): void {
    db.delete(projects).where(eq(projects.id, id)).run();
    db.delete(accessRules).where(eq(accessRules.projectId, id)).run();
  }

  getProjectGroup(id: number): ProjectGroup | undefined {
    return db.select().from(projectGroups).where(eq(projectGroups.id, id)).get();
  }

  getProjectGroups(): ProjectGroup[] {
    return db.select().from(projectGroups).all();
  }

  createProjectGroup(data: InsertProjectGroup): ProjectGroup {
    return db.insert(projectGroups).values(data).returning().get();
  }

  updateProjectGroup(id: number, data: Partial<InsertProjectGroup>): ProjectGroup | undefined {
    return db.update(projectGroups).set(data).where(eq(projectGroups.id, id)).returning().get();
  }

  deleteProjectGroup(id: number): void {
    db.delete(projectGroups).where(eq(projectGroups.id, id)).run();
    // Unlink projects from this group
    db.update(projects).set({ groupId: null }).where(eq(projects.groupId, id)).run();
    db.delete(accessRules).where(eq(accessRules.groupId, id)).run();
  }

  getAccessRules(): AccessRule[] {
    return db.select().from(accessRules).all();
  }

  getAccessRulesForUser(userId: number): AccessRule[] {
    return db.select().from(accessRules).where(eq(accessRules.userId, userId)).all();
  }

  createAccessRule(data: InsertAccessRule): AccessRule {
    return db.insert(accessRules).values(data).returning().get();
  }

  updateAccessRule(id: number, data: Partial<InsertAccessRule>): AccessRule | undefined {
    return db.update(accessRules).set(data).where(eq(accessRules.id, id)).returning().get();
  }

  deleteAccessRule(id: number): void {
    db.delete(accessRules).where(eq(accessRules.id, id)).run();
  }

  getUserAccessibleProjectIds(userId: number): number[] {
    const rules = this.getAccessRulesForUser(userId);
    const projectIds = new Set<number>();
    
    for (const rule of rules) {
      if (rule.projectId) {
        projectIds.add(rule.projectId);
      }
      if (rule.groupId) {
        const groupProjects = this.getProjectsByGroup(rule.groupId);
        for (const p of groupProjects) {
          projectIds.add(p.id);
        }
      }
    }
    return Array.from(projectIds);
  }

  getUserAccessibleProjectMap(userId: number): Map<number, string> {
    const rules = this.getAccessRulesForUser(userId);
    const levelOrder: Record<string, number> = { "": 0, view: 1, edit: 2, manage: 3 };
    const map = new Map<number, string>();
    
    const updateMap = (pid: number, level: string) => {
      const current = map.get(pid) || "";
      if ((levelOrder[level] || 0) > (levelOrder[current] || 0)) {
        map.set(pid, level);
      }
    };
    
    for (const rule of rules) {
      if (rule.projectId) {
        updateMap(rule.projectId, rule.level);
      }
      if (rule.groupId) {
        const groupProjects = this.getProjectsByGroup(rule.groupId);
        for (const p of groupProjects) {
          updateMap(p.id, rule.level);
        }
      }
    }
    return map;
  }

  getSetting(key: string): string | undefined {
    const row = db.select().from(appSettings).where(eq(appSettings.key, key)).get();
    return row?.value;
  }

  setSetting(key: string, value: string): void {
    const existing = db.select().from(appSettings).where(eq(appSettings.key, key)).get();
    if (existing) {
      db.update(appSettings).set({ value }).where(eq(appSettings.key, key)).run();
    } else {
      db.insert(appSettings).values({ key, value }).run();
    }
  }
}

export const storage = new DatabaseStorage();
