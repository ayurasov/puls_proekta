import { Client } from "ldapts";
import { storage } from "./storage";
import { ldapConfigSchema, type LdapConfig } from "@shared/schema";

/**
 * Get current LDAP config from the database
 */
export function getLdapConfig(): LdapConfig {
  const raw = storage.getSetting("ldap");
  if (!raw) return ldapConfigSchema.parse({});
  try {
    return ldapConfigSchema.parse(JSON.parse(raw));
  } catch {
    return ldapConfigSchema.parse({});
  }
}

/**
 * Test LDAP connection — tries to bind with the service account
 */
export async function testLdapConnection(): Promise<{ ok: boolean; error?: string }> {
  const config = getLdapConfig();
  if (!config.serverUrl) return { ok: false, error: "Адрес сервера не указан" };
  if (!config.bindDN) return { ok: false, error: "Bind DN не указан" };

  const client = new Client({
    url: config.serverUrl,
    timeout: 5000,
    connectTimeout: 5000,
  });

  try {
    await client.bind(config.bindDN, config.bindPassword);
    await client.unbind();
    return { ok: true };
  } catch (err: any) {
    try { await client.unbind(); } catch {}
    const msg = err?.message || String(err);
    if (msg.includes("ECONNREFUSED")) return { ok: false, error: "Не удалось подключиться к серверу" };
    if (msg.includes("InvalidCredentials") || msg.includes("49")) return { ok: false, error: "Неверные учётные данные Bind DN" };
    return { ok: false, error: msg };
  }
}

/**
 * Authenticate a user via LDAP
 * 1. Bind with service account
 * 2. Search for user by filter
 * 3. Bind with user's DN + password
 * 4. Return user attributes
 */
export async function authenticateLdap(
  username: string,
  password: string
): Promise<{ ok: true; user: { username: string; name: string; email: string } } | { ok: false; error: string }> {
  const config = getLdapConfig();

  if (!config.enabled) return { ok: false, error: "LDAP не включён" };
  if (!config.serverUrl || !config.baseDN) return { ok: false, error: "LDAP не настроен" };

  const client = new Client({
    url: config.serverUrl,
    timeout: 10000,
    connectTimeout: 5000,
  });

  try {
    // Step 1: Bind with service account
    await client.bind(config.bindDN, config.bindPassword);

    // Step 2: Search for user
    const filter = config.searchFilter.replace(/\{\{username\}\}/g, escapeLdapFilter(username));
    const attrs = config.userAttributes;

    const { searchEntries } = await client.search(config.baseDN, {
      scope: "sub",
      filter,
      attributes: [attrs.username, attrs.name, attrs.email, "dn"],
    });

    if (searchEntries.length === 0) {
      await client.unbind();
      return { ok: false, error: "Пользователь не найден в каталоге" };
    }

    const entry = searchEntries[0];
    const userDN = entry.dn;

    // Step 3: Unbind service account, bind as the user
    await client.unbind();

    const userClient = new Client({
      url: config.serverUrl,
      timeout: 10000,
      connectTimeout: 5000,
    });

    try {
      await userClient.bind(userDN, password);
      await userClient.unbind();
    } catch (bindErr: any) {
      try { await userClient.unbind(); } catch {}
      const msg = bindErr?.message || "";
      if (msg.includes("InvalidCredentials") || msg.includes("49")) {
        return { ok: false, error: "Неверный пароль" };
      }
      return { ok: false, error: "Ошибка аутентификации: " + msg };
    }

    // Step 4: Extract attributes
    const getAttr = (key: string): string => {
      const val = entry[key];
      if (!val) return "";
      if (Array.isArray(val)) return String(val[0] || "");
      return String(val);
    };

    return {
      ok: true,
      user: {
        username: getAttr(attrs.username) || username,
        name: getAttr(attrs.name) || username,
        email: getAttr(attrs.email) || "",
      },
    };
  } catch (err: any) {
    try { await client.unbind(); } catch {}
    const msg = err?.message || String(err);
    if (msg.includes("ECONNREFUSED")) return { ok: false, error: "Не удалось подключиться к LDAP-серверу" };
    return { ok: false, error: "Ошибка LDAP: " + msg };
  }
}

/**
 * Escape special characters in LDAP filter values (RFC 4515)
 */
function escapeLdapFilter(value: string): string {
  return value
    .replace(/\\/g, "\\5c")
    .replace(/\*/g, "\\2a")
    .replace(/\(/g, "\\28")
    .replace(/\)/g, "\\29")
    .replace(/\x00/g, "\\00");
}
