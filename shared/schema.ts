import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

// Users
export const users = sqliteTable("users", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  username: text("username").notNull().unique(),
  password: text("password").notNull(),
  name: text("name").notNull(),
  email: text("email").default(""),
  role: text("role").notNull().default("viewer"), // admin | manager | editor | viewer
  active: integer("active", { mode: "boolean" }).notNull().default(true),
  authSource: text("auth_source").notNull().default("local"), // local | ldap
  createdAt: text("created_at").notNull().default(""),
});

export const insertUserSchema = createInsertSchema(users).omit({ id: true, createdAt: true });
export type InsertUser = z.infer<typeof insertUserSchema>;
export type User = typeof users.$inferSelect;

// Project groups (directions)
export const projectGroups = sqliteTable("project_groups", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull(),
  description: text("description").default(""),
  color: text("color").default("blue"),
});

export const insertProjectGroupSchema = createInsertSchema(projectGroups).omit({ id: true });
export type InsertProjectGroup = z.infer<typeof insertProjectGroupSchema>;
export type ProjectGroup = typeof projectGroups.$inferSelect;

// Projects
export const projects = sqliteTable("projects", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  title: text("title").notNull(),
  subtitle: text("subtitle").default(""),
  groupId: integer("group_id"),
  data: text("data").notNull().default("{}"), // JSON: full gantt state
  sortOrder: integer("sort_order").notNull().default(0),
  createdBy: integer("created_by"),
  createdAt: text("created_at").notNull().default(""),
  updatedAt: text("updated_at").notNull().default(""),
});

export const insertProjectSchema = createInsertSchema(projects).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertProject = z.infer<typeof insertProjectSchema>;
export type Project = typeof projects.$inferSelect;

// Access control: who can access what project/group and with what level
export const accessRules = sqliteTable("access_rules", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  userId: integer("user_id").notNull(),
  projectId: integer("project_id"),       // null means group-level access
  groupId: integer("group_id"),           // null means project-level access
  level: text("level").notNull().default("view"), // view | edit | manage
});

export const insertAccessRuleSchema = createInsertSchema(accessRules).omit({ id: true });
export type InsertAccessRule = z.infer<typeof insertAccessRuleSchema>;
export type AccessRule = typeof accessRules.$inferSelect;

// App settings (key-value store for LDAP config etc.)
export const appSettings = sqliteTable("app_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull().default(""),
});

export const insertAppSettingSchema = createInsertSchema(appSettings);
export type InsertAppSetting = z.infer<typeof insertAppSettingSchema>;
export type AppSetting = typeof appSettings.$inferSelect;

// LDAP config shape (stored as JSON in appSettings under key 'ldap')
export const ldapConfigSchema = z.object({
  enabled: z.boolean().default(false),
  serverUrl: z.string().default(""),
  baseDN: z.string().default(""),
  bindDN: z.string().default(""),
  bindPassword: z.string().default(""),
  searchFilter: z.string().default("(uid={{username}})"),
  userAttributes: z.object({
    username: z.string().default("uid"),
    name: z.string().default("cn"),
    email: z.string().default("mail"),
  }).default({}),
  // Auth mode: local = only local, ldap = only LDAP, hybrid = try LDAP first then local
  authMode: z.enum(["local", "ldap", "hybrid"]).default("local"),
  // Default role for auto-created LDAP users
  defaultRole: z.enum(["viewer", "editor", "manager"]).default("viewer"),
});

export type LdapConfig = z.infer<typeof ldapConfigSchema>;
