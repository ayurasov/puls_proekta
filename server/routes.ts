import type { Express, Request, Response, NextFunction } from "express";
import type { Server } from "http";
import session from "express-session";
import passport, { seedAdmin } from "./auth";
import bcrypt from "bcryptjs";
import { storage } from "./storage";
import { ldapConfigSchema } from "@shared/schema";
import { testLdapConnection, getLdapConfig as readLdapConfig } from "./ldap";

// Middleware helpers
function requireAuth(req: Request, res: Response, next: NextFunction) {
  if (!req.isAuthenticated()) return res.status(401).json({ error: "Не авторизован" });
  next();
}

function requireAdmin(req: Request, res: Response, next: NextFunction) {
  if (!req.isAuthenticated()) return res.status(401).json({ error: "Не авторизован" });
  if (req.user!.role !== "admin") return res.status(403).json({ error: "Недостаточно прав" });
  next();
}

function requireManagerOrAdmin(req: Request, res: Response, next: NextFunction) {
  if (!req.isAuthenticated()) return res.status(401).json({ error: "Не авторизован" });
  if (req.user!.role !== "admin" && req.user!.role !== "manager") return res.status(403).json({ error: "Недостаточно прав" });
  next();
}

function canAccessProject(userId: number, userRole: string, projectId: number): { access: boolean; level: string } {
  if (userRole === "admin") return { access: true, level: "manage" };
  
  const project = storage.getProject(projectId);
  if (!project) return { access: false, level: "" };
  
  // Managers who created the project get full manage access
  if (userRole === "manager" && project.createdBy === userId) {
    return { access: true, level: "manage" };
  }
  
  const rules = storage.getAccessRulesForUser(userId);
  let bestLevel = "";
  
  for (const rule of rules) {
    if (rule.projectId === projectId) {
      bestLevel = betterLevel(bestLevel, rule.level);
    }
    if (rule.groupId && project.groupId === rule.groupId) {
      bestLevel = betterLevel(bestLevel, rule.level);
    }
  }
  
  return { access: bestLevel !== "", level: bestLevel };
}

function betterLevel(a: string, b: string): string {
  const order: Record<string, number> = { "": 0, view: 1, edit: 2, manage: 3 };
  return (order[b] || 0) > (order[a] || 0) ? b : a;
}

export async function registerRoutes(server: Server, app: Express) {
  // Session setup — uses SQLite-backed store via memorystore (no cookies needed for same-origin)
  app.use(
    session({
      secret: "puls-proekta-secret-key-2026-secure",
      resave: false,
      saveUninitialized: false,
      cookie: { maxAge: 365 * 24 * 60 * 60 * 1000 }, // 1 year
      rolling: true, // refresh expiry on each request
    })
  );

  app.use(passport.initialize());
  app.use(passport.session());

  // Seed admin
  await seedAdmin();

  // ── Auth ──────────────────────────────────────────────
  // Public endpoint: auth mode info (for login page)
  app.get("/api/auth/mode", (_req, res) => {
    const config = readLdapConfig();
    if (!config.enabled) {
      return res.json({ mode: "local", ldapEnabled: false });
    }
    res.json({ mode: config.authMode, ldapEnabled: true });
  });

  app.post("/api/auth/login", (req, res, next) => {
    passport.authenticate("local", (err: any, user: any, info: any) => {
      if (err) return next(err);
      if (!user) return res.status(401).json({ error: info?.message || "Ошибка входа" });
      req.logIn(user, (err) => {
        if (err) return next(err);
        res.json({ user });
      });
    })(req, res, next);
  });

  app.post("/api/auth/logout", (req, res) => {
    req.logout(() => {
      res.json({ ok: true });
    });
  });

  app.get("/api/auth/me", (req, res) => {
    if (!req.isAuthenticated()) return res.status(401).json({ error: "Не авторизован" });
    res.json({ user: req.user });
  });

  // ── Users (admin only) ────────────────────────────────
  app.get("/api/users", requireAdmin, (_req, res) => {
    const all = storage.getUsers().map((u) => ({ ...u, password: undefined }));
    res.json(all);
  });

  app.post("/api/users", requireAdmin, async (req, res) => {
    const { username, password, name, email, role } = req.body;
    if (!username || !password || !name) return res.status(400).json({ error: "Заполните обязательные поля" });
    
    const existing = storage.getUserByUsername(username);
    if (existing) return res.status(400).json({ error: "Пользователь с таким логином уже существует" });
    
    const hash = await bcrypt.hash(password, 12);
    const user = storage.createUser({ username, password: hash, name, email: email || "", role: role || "viewer", active: true });
    res.status(201).json({ ...user, password: undefined });
  });

  app.put("/api/users/:id", requireAdmin, async (req, res) => {
    const id = parseInt(req.params.id);
    const { name, email, role, active, password, username } = req.body;

    // Protect: cannot deactivate yourself
    if (active === false && id === req.user!.id) {
      return res.status(400).json({ error: "Нельзя деактивировать самого себя" });
    }
    // Protect: cannot deactivate the primary admin (id=1)
    if (active === false && id === 1) {
      return res.status(400).json({ error: "Нельзя деактивировать главного администратора" });
    }
    // Protect: cannot change role of primary admin
    if (role !== undefined && id === 1 && role !== "admin") {
      return res.status(400).json({ error: "Нельзя изменить роль главного администратора" });
    }

    const update: any = {};
    if (name !== undefined) update.name = name;
    if (email !== undefined) update.email = email;
    if (role !== undefined) update.role = role;
    if (active !== undefined) update.active = active;
    if (username !== undefined) {
      const existing = storage.getUserByUsername(username);
      if (existing && existing.id !== id) return res.status(400).json({ error: "Логин уже занят" });
      update.username = username;
    }
    if (password) update.password = await bcrypt.hash(password, 12);
    
    const user = storage.updateUser(id, update);
    if (!user) return res.status(404).json({ error: "Пользователь не найден" });
    res.json({ ...user, password: undefined });
  });

  app.delete("/api/users/:id", requireAdmin, (req, res) => {
    const id = parseInt(req.params.id);
    if (id === req.user!.id) return res.status(400).json({ error: "Нельзя удалить себя" });
    storage.deleteUser(id);
    res.json({ ok: true });
  });

  // ── Project Groups ────────────────────────────────────
  app.get("/api/groups", requireAuth, (_req, res) => {
    res.json(storage.getProjectGroups());
  });

  app.post("/api/groups", requireManagerOrAdmin, (req, res) => {
    const { name, description, color } = req.body;
    if (!name) return res.status(400).json({ error: "Укажите название" });
    const group = storage.createProjectGroup({ name, description: description || "", color: color || "blue" });
    res.status(201).json(group);
  });

  app.put("/api/groups/:id", requireManagerOrAdmin, (req, res) => {
    const id = parseInt(req.params.id);
    const group = storage.updateProjectGroup(id, req.body);
    if (!group) return res.status(404).json({ error: "Группа не найдена" });
    res.json(group);
  });

  app.delete("/api/groups/:id", requireAdmin, (req, res) => {
    storage.deleteProjectGroup(parseInt(req.params.id));
    res.json({ ok: true });
  });

  // ── Projects ──────────────────────────────────────────
  app.get("/api/projects", requireAuth, (req, res) => {
    const user = req.user!;
    const allProjects = storage.getProjects();
    
    if (user.role === "admin") {
      const all = allProjects.map((p) => ({ ...p, data: undefined, _accessLevel: "manage" }));
      return res.json(all);
    }
    
    // Build map of accessible project IDs with their best access level
    const accessMap = storage.getUserAccessibleProjectMap(user.id);
    
    // Managers also see projects they created
    if (user.role === "manager") {
      for (const p of allProjects) {
        if (p.createdBy === user.id && !accessMap.has(p.id)) {
          accessMap.set(p.id, "manage");
        }
      }
    }
    
    const filtered = allProjects
      .filter((p) => accessMap.has(p.id))
      .map((p) => ({ ...p, data: undefined, _accessLevel: accessMap.get(p.id) }));
    res.json(filtered);
  });

  app.get("/api/projects/:id", requireAuth, (req, res) => {
    const id = parseInt(req.params.id);
    const user = req.user!;
    const { access, level } = canAccessProject(user.id, user.role, id);
    if (!access) return res.status(403).json({ error: "Нет доступа к проекту" });
    
    const project = storage.getProject(id);
    if (!project) return res.status(404).json({ error: "Проект не найден" });
    res.json({ ...project, _accessLevel: level });
  });

  app.post("/api/projects", requireManagerOrAdmin, (req, res) => {
    const { title, subtitle, groupId, data } = req.body;
    if (!title) return res.status(400).json({ error: "Укажите название" });
    const project = storage.createProject({
      title,
      subtitle: subtitle || "",
      groupId: groupId || null,
      data: data || "{}",
      createdBy: req.user!.id,
    });
    res.status(201).json(project);
  });

  app.put("/api/projects/:id", requireAuth, (req, res) => {
    const id = parseInt(req.params.id);
    const user = req.user!;
    const { access, level } = canAccessProject(user.id, user.role, id);
    if (!access) return res.status(403).json({ error: "Нет доступа к проекту" });
    if (level === "view") return res.status(403).json({ error: "Только просмотр" });
    
    const { data, title, subtitle, groupId, sortOrder } = req.body;
    const update: any = {};
    if (data !== undefined) update.data = data;
    if (title !== undefined) update.title = title;
    if (subtitle !== undefined) update.subtitle = subtitle;
    if (groupId !== undefined) update.groupId = groupId;
    if (sortOrder !== undefined) update.sortOrder = sortOrder;
    
    const project = storage.updateProject(id, update);
    if (!project) return res.status(404).json({ error: "Проект не найден" });
    res.json(project);
  });

  app.delete("/api/projects/:id", requireAdmin, (req, res) => {
    storage.deleteProject(parseInt(req.params.id));
    res.json({ ok: true });
  });

  // ── Access Rules ──────────────────────────────────────
  app.get("/api/access", requireAdmin, (_req, res) => {
    res.json(storage.getAccessRules());
  });

  app.post("/api/access", requireAdmin, (req, res) => {
    const { userId, projectId, groupId, level } = req.body;
    if (!userId) return res.status(400).json({ error: "Укажите пользователя" });
    if (!projectId && !groupId) return res.status(400).json({ error: "Укажите проект или группу" });
    const rule = storage.createAccessRule({
      userId,
      projectId: projectId || null,
      groupId: groupId || null,
      level: level || "view",
    });
    res.status(201).json(rule);
  });

  app.put("/api/access/:id", requireAdmin, (req, res) => {
    const id = parseInt(req.params.id);
    const { userId, projectId, groupId, level } = req.body;
    const update: any = {};
    if (userId !== undefined) update.userId = userId;
    if (level !== undefined) update.level = level;
    if (projectId !== undefined) { update.projectId = projectId || null; update.groupId = null; }
    if (groupId !== undefined) { update.groupId = groupId || null; update.projectId = null; }
    const rule = storage.updateAccessRule(id, update);
    if (!rule) return res.status(404).json({ error: "Правило не найдено" });
    res.json(rule);
  });

  app.delete("/api/access/:id", requireAdmin, (req, res) => {
    storage.deleteAccessRule(parseInt(req.params.id));
    res.json({ ok: true });
  });

  // ── Settings (admin only) ────────────────────────────────
  app.get("/api/settings/ldap", requireAdmin, (_req, res) => {
    const raw = storage.getSetting("ldap");
    if (!raw) {
      return res.json(ldapConfigSchema.parse({}));
    }
    try {
      const config = ldapConfigSchema.parse(JSON.parse(raw));
      // Don't expose bind password in full
      res.json({ ...config, bindPassword: config.bindPassword ? "••••••••" : "" });
    } catch {
      res.json(ldapConfigSchema.parse({}));
    }
  });

  app.put("/api/settings/ldap", requireAdmin, (req, res) => {
    // If bindPassword is the masked value, preserve the existing one
    const existing = storage.getSetting("ldap");
    let existingConfig: any = {};
    if (existing) {
      try { existingConfig = JSON.parse(existing); } catch {}
    }

    const body = { ...req.body };
    if (body.bindPassword === "••••••••" && existingConfig.bindPassword) {
      body.bindPassword = existingConfig.bindPassword;
    }

    const parsed = ldapConfigSchema.safeParse(body);
    if (!parsed.success) {
      return res.status(400).json({ error: "Некорректные данные", details: parsed.error.issues });
    }

    storage.setSetting("ldap", JSON.stringify(parsed.data));
    res.json({ ok: true });
  });

  // Test LDAP connection
  app.post("/api/settings/ldap/test", requireAdmin, async (_req, res) => {
    const result = await testLdapConnection();
    res.json(result);
  });
}
