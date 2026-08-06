import { apiRequest } from "./queryClient";

export async function login(username: string, password: string) {
  const res = await apiRequest("POST", "/api/auth/login", { username, password });
  return res.json();
}

export async function logout() {
  const res = await apiRequest("POST", "/api/auth/logout");
  return res.json();
}

export async function getMe() {
  const res = await apiRequest("GET", "/api/auth/me");
  return res.json();
}

// Users
export async function getUsers() {
  const res = await apiRequest("GET", "/api/users");
  return res.json();
}

export async function createUser(data: any) {
  const res = await apiRequest("POST", "/api/users", data);
  return res.json();
}

export async function updateUser(id: number, data: any) {
  const res = await apiRequest("PUT", `/api/users/${id}`, data);
  return res.json();
}

export async function deleteUser(id: number) {
  const res = await apiRequest("DELETE", `/api/users/${id}`);
  return res.json();
}

// Groups
export async function getGroups() {
  const res = await apiRequest("GET", "/api/groups");
  return res.json();
}

export async function createGroup(data: any) {
  const res = await apiRequest("POST", "/api/groups", data);
  return res.json();
}

export async function updateGroup(id: number, data: any) {
  const res = await apiRequest("PUT", `/api/groups/${id}`, data);
  return res.json();
}

export async function deleteGroup(id: number) {
  const res = await apiRequest("DELETE", `/api/groups/${id}`);
  return res.json();
}

// Projects
export async function getProjects() {
  const res = await apiRequest("GET", "/api/projects");
  return res.json();
}

export async function getProject(id: number) {
  const res = await apiRequest("GET", `/api/projects/${id}`);
  return res.json();
}

export async function createProject(data: any) {
  const res = await apiRequest("POST", "/api/projects", data);
  return res.json();
}

export async function updateProject(id: number, data: any) {
  const res = await apiRequest("PUT", `/api/projects/${id}`, data);
  return res.json();
}

export async function deleteProject(id: number) {
  const res = await apiRequest("DELETE", `/api/projects/${id}`);
  return res.json();
}

// Access
export async function getAccess() {
  const res = await apiRequest("GET", "/api/access");
  return res.json();
}

export async function createAccess(data: any) {
  const res = await apiRequest("POST", "/api/access", data);
  return res.json();
}

export async function updateAccess(id: number, data: any) {
  const res = await apiRequest("PUT", `/api/access/${id}`, data);
  return res.json();
}

export async function deleteAccess(id: number) {
  const res = await apiRequest("DELETE", `/api/access/${id}`);
  return res.json();
}

// Project ordering
export async function updateProjectOrder(projectId: number, groupId: number | null, sortOrder: number) {
  const res = await apiRequest("PUT", `/api/projects/${projectId}`, { groupId, sortOrder });
  return res.json();
}

// Settings
export async function getLdapConfig() {
  const res = await apiRequest("GET", "/api/settings/ldap");
  return res.json();
}

export async function saveLdapConfig(data: any) {
  const res = await apiRequest("PUT", "/api/settings/ldap", data);
  return res.json();
}

export async function testLdapConnection() {
  const res = await apiRequest("POST", "/api/settings/ldap/test");
  return res.json();
}

export async function getAuthMode() {
  const res = await apiRequest("GET", "/api/auth/mode");
  return res.json();
}
