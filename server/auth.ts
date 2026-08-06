import passport from "passport";
import { Strategy as LocalStrategy } from "passport-local";
import bcrypt from "bcryptjs";
import { storage } from "./storage";
import { authenticateLdap, getLdapConfig } from "./ldap";

declare global {
  namespace Express {
    interface User {
      id: number;
      username: string;
      name: string;
      email: string | null;
      role: string;
      active: boolean | null;
      authSource: string;
    }
  }
}

/**
 * Authenticate via local DB password
 */
async function tryLocalAuth(username: string, password: string): Promise<Express.User | null> {
  const user = storage.getUserByUsername(username);
  if (!user || !user.active) return null;
  // LDAP-only users don't have local passwords
  if (user.authSource === "ldap" && (!user.password || user.password === "")) return null;
  const valid = await bcrypt.compare(password, user.password);
  if (!valid) return null;
  return {
    id: user.id,
    username: user.username,
    name: user.name,
    email: user.email,
    role: user.role,
    active: user.active,
    authSource: user.authSource || "local",
  };
}

/**
 * Authenticate via LDAP and auto-provision/update user in local DB
 */
async function tryLdapAuth(username: string, password: string): Promise<Express.User | null> {
  const result = await authenticateLdap(username, password);
  if (!result.ok) return null;

  const ldapConfig = getLdapConfig();
  const ldapUser = result.user;

  // Check if user already exists locally
  let dbUser = storage.getUserByUsername(ldapUser.username);

  if (dbUser) {
    // Update name/email from LDAP if changed
    if (dbUser.name !== ldapUser.name || dbUser.email !== ldapUser.email || dbUser.authSource !== "ldap") {
      dbUser = storage.updateUser(dbUser.id, {
        name: ldapUser.name,
        email: ldapUser.email,
        authSource: "ldap",
      })!;
    }
    if (!dbUser.active) return null;
  } else {
    // Auto-provision new LDAP user
    dbUser = storage.createUser({
      username: ldapUser.username,
      password: "", // LDAP users don't need local password
      name: ldapUser.name,
      email: ldapUser.email,
      role: ldapConfig.defaultRole || "viewer",
      active: true,
      authSource: "ldap",
    });
  }

  return {
    id: dbUser.id,
    username: dbUser.username,
    name: dbUser.name,
    email: dbUser.email,
    role: dbUser.role,
    active: dbUser.active,
    authSource: dbUser.authSource || "ldap",
  };
}

passport.use(
  new LocalStrategy(async (username, password, done) => {
    try {
      const config = getLdapConfig();
      const mode = config.enabled ? config.authMode : "local";

      let user: Express.User | null = null;

      if (mode === "local") {
        // Only local
        user = await tryLocalAuth(username, password);
        if (!user) return done(null, false, { message: "Неверный логин или пароль" });

      } else if (mode === "ldap") {
        // Only LDAP (admin fallback to local)
        const dbUser = storage.getUserByUsername(username);
        if (dbUser?.role === "admin" && dbUser.authSource === "local") {
          // Admin with local auth source always gets local fallback
          user = await tryLocalAuth(username, password);
          if (user) return done(null, user);
        }
        user = await tryLdapAuth(username, password);
        if (!user) return done(null, false, { message: "Ошибка входа через LDAP" });

      } else if (mode === "hybrid") {
        // Try LDAP first, then local
        user = await tryLdapAuth(username, password);
        if (!user) {
          user = await tryLocalAuth(username, password);
        }
        if (!user) return done(null, false, { message: "Неверный логин или пароль" });
      }

      // Check deactivated
      const freshUser = storage.getUserByUsername(username);
      if (freshUser && !freshUser.active) {
        return done(null, false, { message: "Учётная запись деактивирована" });
      }

      return done(null, user);
    } catch (err) {
      return done(err);
    }
  })
);

passport.serializeUser((user: any, done) => {
  done(null, user.id);
});

passport.deserializeUser((id: number, done) => {
  try {
    const user = storage.getUser(id);
    if (!user || !user.active) return done(null, false);
    done(null, {
      id: user.id,
      username: user.username,
      name: user.name,
      email: user.email,
      role: user.role,
      active: user.active,
      authSource: user.authSource || "local",
    });
  } catch (err) {
    done(err);
  }
});

// Seed admin user if none exists
export async function seedAdmin() {
  const existing = storage.getUserByUsername("admin");
  if (!existing) {
    const hash = await bcrypt.hash("pulsbald198!", 12);
    storage.createUser({
      username: "admin",
      password: hash,
      name: "Администратор",
      email: "ayurasov@gmail.com",
      role: "admin",
      active: true,
      authSource: "local",
    });
    console.log("Admin user created: admin");
  }
}

export default passport;
