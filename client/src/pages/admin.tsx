import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useAuth } from "../hooks/use-auth";
import { getUsers, createUser, updateUser, deleteUser, getGroups, createGroup, updateGroup, deleteGroup, getAccess, createAccess, updateAccess, deleteAccess, getProjects, getLdapConfig, saveLdapConfig, testLdapConnection } from "../lib/api";
import { queryClient } from "../lib/queryClient";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Switch } from "@/components/ui/switch";
import { ArrowLeft, Plus, Trash2, Users, FolderTree, Shield, Loader2, Pencil, Check, X, Settings2, Server, Wifi, WifiOff, AlertTriangle } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const ROLES = [
  { value: "admin", label: "Администратор" },
  { value: "manager", label: "Менеджер" },
  { value: "editor", label: "Редактор" },
  { value: "viewer", label: "Наблюдатель" },
];

const LEVELS = [
  { value: "view", label: "Просмотр" },
  { value: "edit", label: "Редактирование" },
  { value: "manage", label: "Полное управление" },
];

export default function AdminPage() {
  const { user } = useAuth();
  const [, navigate] = useLocation();

  if (user?.role !== "admin") {
    return <div className="p-8 text-center text-muted-foreground">Доступ запрещён</div>;
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-card">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate("/")} data-testid="button-back">
            <ArrowLeft className="w-4 h-4 mr-1" /> Назад
          </Button>
          <span className="font-semibold text-sm">Администрирование</span>
        </div>
      </header>
      <main className="max-w-6xl mx-auto p-4">
        <Tabs defaultValue="users">
          <TabsList>
            <TabsTrigger value="users"><Users className="w-3.5 h-3.5 mr-1" /> Пользователи</TabsTrigger>
            <TabsTrigger value="groups"><FolderTree className="w-3.5 h-3.5 mr-1" /> Направления</TabsTrigger>
            <TabsTrigger value="access"><Shield className="w-3.5 h-3.5 mr-1" /> Доступы</TabsTrigger>
            <TabsTrigger value="settings"><Settings2 className="w-3.5 h-3.5 mr-1" /> Настройки</TabsTrigger>
          </TabsList>

          <TabsContent value="users" className="mt-4"><UsersTab /></TabsContent>
          <TabsContent value="groups" className="mt-4"><GroupsTab /></TabsContent>
          <TabsContent value="access" className="mt-4"><AccessTab /></TabsContent>
          <TabsContent value="settings" className="mt-4"><SettingsTab /></TabsContent>
        </Tabs>
      </main>
    </div>
  );
}

function UsersTab() {
  const { user: me } = useAuth();
  const { data: users = [], isLoading } = useQuery({ queryKey: ["/api/users"], queryFn: getUsers });
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", username: "", password: "", email: "", role: "viewer" });
  const [editId, setEditId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<any>({});

  const createMut = useMutation({
    mutationFn: (d: any) => createUser(d),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/users"] }); setOpen(false); setForm({ name: "", username: "", password: "", email: "", role: "viewer" }); },
    onError: (e: any) => toast({ title: e?.message || "Ошибка", variant: "destructive" }),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: any }) => updateUser(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/users"] }); setEditId(null); },
    onError: (e: any) => toast({ title: e?.message || "Ошибка", variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/users"] }),
  });

  const startEdit = (u: any) => {
    setEditId(u.id);
    setEditForm({ name: u.name, username: u.username, email: u.email || "", role: u.role, password: "" });
  };

  const saveEdit = () => {
    if (!editId) return;
    const data: any = { name: editForm.name, username: editForm.username, email: editForm.email, role: editForm.role };
    if (editForm.password) data.password = editForm.password;
    updateMut.mutate({ id: editId, data });
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm">Пользователи</CardTitle>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button size="sm" data-testid="button-add-user"><Plus className="w-4 h-4 mr-1" /> Добавить</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Новый пользователь</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <div><Label>ФИО</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="input-user-name" /></div>
              <div><Label>Логин</Label><Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} data-testid="input-user-login" /></div>
              <div><Label>Пароль</Label><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} data-testid="input-user-password" /></div>
              <div><Label>Email</Label><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} data-testid="input-user-email" /></div>
              <div>
                <Label>Роль</Label>
                <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                  <SelectTrigger data-testid="select-user-role"><SelectValue /></SelectTrigger>
                  <SelectContent>{ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <Button className="w-full" onClick={() => createMut.mutate(form)} disabled={!form.name || !form.username || !form.password || createMut.isPending} data-testid="button-save-user">
                {createMut.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null} Создать
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? <div className="p-6 text-center"><Loader2 className="w-5 h-5 animate-spin mx-auto" /></div> : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ФИО</TableHead>
                <TableHead>Логин</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>Активен</TableHead>
                <TableHead className="w-20"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u: any) => (
                <TableRow key={u.id} data-testid={`row-user-${u.id}`}>
                  {editId === u.id ? (
                    <>
                      <TableCell><Input value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} className="h-8 text-sm" /></TableCell>
                      <TableCell><Input value={editForm.username} onChange={(e) => setEditForm({ ...editForm, username: e.target.value })} className="h-8 text-sm" /></TableCell>
                      <TableCell><Input value={editForm.email} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} className="h-8 text-sm" /></TableCell>
                      <TableCell>
                        <Select value={editForm.role} onValueChange={(v) => setEditForm({ ...editForm, role: v })}>
                          <SelectTrigger className="h-8 text-xs w-36"><SelectValue /></SelectTrigger>
                          <SelectContent>{ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell colSpan={1}>
                        <Input type="password" placeholder="Новый пароль" value={editForm.password} onChange={(e) => setEditForm({ ...editForm, password: e.target.value })} className="h-8 text-sm" />
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-green-600" onClick={saveEdit} disabled={updateMut.isPending}>
                            <Check className="w-3.5 h-3.5" />
                          </Button>
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setEditId(null)}>
                            <X className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </>
                  ) : (
                    <>
                      <TableCell className="font-medium text-sm">{u.name}</TableCell>
                      <TableCell className="text-sm">{u.username}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{u.email}</TableCell>
                      <TableCell>
                        <Select value={u.role} onValueChange={(v) => updateMut.mutate({ id: u.id, data: { role: v } })}>
                          <SelectTrigger className="h-7 text-xs w-36"><SelectValue /></SelectTrigger>
                          <SelectContent>{ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <Switch checked={u.active} disabled={u.id === 1 || u.id === me?.id} onCheckedChange={(v) => updateMut.mutate({ id: u.id, data: { active: v } })} />
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => startEdit(u)} data-testid={`button-edit-user-${u.id}`}>
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button variant="ghost" size="sm" className="text-destructive h-7 w-7 p-0" onClick={() => deleteMut.mutate(u.id)} data-testid={`button-delete-user-${u.id}`}>
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function GroupsTab() {
  const { data: groups = [], isLoading } = useQuery({ queryKey: ["/api/groups"], queryFn: getGroups });
  const [name, setName] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  const createMut = useMutation({ mutationFn: (d: any) => createGroup(d), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/groups"] }); setName(""); } });
  const updateMut = useMutation({ mutationFn: ({ id, data }: { id: number; data: any }) => updateGroup(id, data), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/groups"] }); setEditId(null); } });
  const deleteMut = useMutation({ mutationFn: (id: number) => deleteGroup(id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/groups"] }) });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm">Группы проектов / Направления</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название направления" className="flex-1" data-testid="input-group-name" />
          <Button size="sm" onClick={() => createMut.mutate({ name })} disabled={!name.trim() || createMut.isPending} data-testid="button-add-group">
            <Plus className="w-4 h-4 mr-1" /> Добавить
          </Button>
        </div>
        {isLoading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : (
          <div className="space-y-1">
            {groups.map((g: any) => (
              <div key={g.id} className="flex items-center justify-between p-2 rounded-md border text-sm" data-testid={`row-group-${g.id}`}>
                {editId === g.id ? (
                  <>
                    <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="h-8 text-sm flex-1 mr-2" autoFocus onKeyDown={(e) => { if (e.key === "Enter" && editName.trim()) updateMut.mutate({ id: g.id, data: { name: editName } }); }} />
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-green-600" onClick={() => updateMut.mutate({ id: g.id, data: { name: editName } })} disabled={!editName.trim()}>
                        <Check className="w-3.5 h-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setEditId(null)}>
                        <X className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <span>{g.name}</span>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => { setEditId(g.id); setEditName(g.name); }}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button variant="ghost" size="sm" className="text-destructive h-7 w-7 p-0" onClick={() => deleteMut.mutate(g.id)}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
            ))}
            {groups.length === 0 && <p className="text-sm text-muted-foreground text-center py-4">Нет направлений</p>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AccessTab() {
  const { data: access = [], isLoading } = useQuery({ queryKey: ["/api/access"], queryFn: getAccess });
  const { data: users = [] } = useQuery({ queryKey: ["/api/users"], queryFn: getUsers });
  const { data: projects = [] } = useQuery({ queryKey: ["/api/projects"], queryFn: getProjects });
  const { data: groups = [] } = useQuery({ queryKey: ["/api/groups"], queryFn: getGroups });

  const [form, setForm] = useState({ userId: "", targetType: "project", targetId: "", level: "view" });
  const [editId, setEditId] = useState<number | null>(null);
  const [editLevel, setEditLevel] = useState("");

  const createMut = useMutation({
    mutationFn: (d: any) => createAccess(d),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/access"] }); setForm({ userId: "", targetType: "project", targetId: "", level: "view" }); },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: any }) => updateAccess(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["/api/access"] }); setEditId(null); },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteAccess(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/access"] }),
  });

  const userMap = new Map(users.map((u: any) => [u.id, u.name]));
  const projectMap = new Map(projects.map((p: any) => [p.id, p.title]));
  const groupMap = new Map(groups.map((g: any) => [g.id, g.name]));
  const levelLabel = (l: string) => LEVELS.find((x) => x.value === l)?.label || l;

  const handleAdd = () => {
    const data: any = { userId: parseInt(form.userId), level: form.level };
    if (form.targetType === "project") data.projectId = parseInt(form.targetId);
    else data.groupId = parseInt(form.targetId);
    createMut.mutate(data);
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Правила доступа</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 items-end">
          <div>
            <Label className="text-xs">Пользователь</Label>
            <Select value={form.userId} onValueChange={(v) => setForm({ ...form, userId: v })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Выбрать" /></SelectTrigger>
              <SelectContent>{users.filter((u: any) => u.role !== "admin").map((u: any) => <SelectItem key={u.id} value={String(u.id)}>{u.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Тип</Label>
            <Select value={form.targetType} onValueChange={(v) => setForm({ ...form, targetType: v, targetId: "" })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="project">Проект</SelectItem>
                <SelectItem value="group">Направление</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">{form.targetType === "project" ? "Проект" : "Направление"}</Label>
            <Select value={form.targetId} onValueChange={(v) => setForm({ ...form, targetId: v })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Выбрать" /></SelectTrigger>
              <SelectContent>
                {form.targetType === "project"
                  ? projects.map((p: any) => <SelectItem key={p.id} value={String(p.id)}>{p.title}</SelectItem>)
                  : groups.map((g: any) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Уровень</Label>
            <Select value={form.level} onValueChange={(v) => setForm({ ...form, level: v })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>{LEVELS.map((l) => <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <Button size="sm" className="h-8" onClick={handleAdd} disabled={!form.userId || !form.targetId || createMut.isPending} data-testid="button-add-access">
            <Plus className="w-4 h-4 mr-1" /> Добавить
          </Button>
        </div>

        {isLoading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Пользователь</TableHead>
                <TableHead>Объект</TableHead>
                <TableHead>Уровень</TableHead>
                <TableHead className="w-20"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {access.map((a: any) => (
                <TableRow key={a.id} data-testid={`row-access-${a.id}`}>
                  <TableCell className="text-sm">{userMap.get(a.userId) || `#${a.userId}`}</TableCell>
                  <TableCell className="text-sm">
                    {a.projectId ? <><Badge variant="outline" className="text-[10px] mr-1">Проект</Badge>{projectMap.get(a.projectId) || `#${a.projectId}`}</> : null}
                    {a.groupId ? <><Badge variant="outline" className="text-[10px] mr-1">Направление</Badge>{groupMap.get(a.groupId) || `#${a.groupId}`}</> : null}
                  </TableCell>
                  <TableCell>
                    {editId === a.id ? (
                      <Select value={editLevel} onValueChange={setEditLevel}>
                        <SelectTrigger className="h-7 text-xs w-40"><SelectValue /></SelectTrigger>
                        <SelectContent>{LEVELS.map((l) => <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>)}</SelectContent>
                      </Select>
                    ) : (
                      <Badge variant="secondary" className="text-xs">{levelLabel(a.level)}</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {editId === a.id ? (
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-green-600" onClick={() => updateMut.mutate({ id: a.id, data: { level: editLevel } })}>
                          <Check className="w-3.5 h-3.5" />
                        </Button>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setEditId(null)}>
                          <X className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => { setEditId(a.id); setEditLevel(a.level); }}>
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                        <Button variant="ghost" size="sm" className="text-destructive h-7 w-7 p-0" onClick={() => deleteMut.mutate(a.id)}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {access.length === 0 && <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground py-4">Нет правил доступа</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

const AUTH_MODES = [
  { value: "local", label: "Локальная", desc: "Только встроенные учётные записи" },
  { value: "ldap", label: "Только LDAP", desc: "Вход только через каталог (admin сохраняет локальный вход)" },
  { value: "hybrid", label: "Гибридная", desc: "Сначала LDAP, затем локальная Авторизация" },
];

const DEFAULT_ROLES = [
  { value: "viewer", label: "Наблюдатель" },
  { value: "editor", label: "Редактор" },
  { value: "manager", label: "Менеджер" },
];

function SettingsTab() {
  const { toast } = useToast();
  const { data: ldapConfig, isLoading } = useQuery({
    queryKey: ["/api/settings/ldap"],
    queryFn: getLdapConfig,
  });

  const [form, setForm] = useState<any>(null);
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  // Sync form from fetched data
  const currentForm = form ?? ldapConfig ?? {
    enabled: false,
    serverUrl: "",
    baseDN: "",
    bindDN: "",
    bindPassword: "",
    searchFilter: "(uid={{username}})",
    userAttributes: { username: "uid", name: "cn", email: "mail" },
    authMode: "local",
    defaultRole: "viewer",
  };

  const updateField = (field: string, value: any) => {
    const newForm = { ...currentForm, [field]: value };
    setForm(newForm);
    setDirty(true);
    setTestResult(null);
  };

  const updateAttr = (field: string, value: string) => {
    const newForm = { ...currentForm, userAttributes: { ...currentForm.userAttributes, [field]: value } };
    setForm(newForm);
    setDirty(true);
  };

  const saveMut = useMutation({
    mutationFn: (data: any) => saveLdapConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/settings/ldap"] });
      setDirty(false);
      setForm(null);
      toast({ title: "Настройки LDAP сохранены" });
    },
    onError: (e: any) => toast({ title: e?.message || "Ошибка сохранения", variant: "destructive" }),
  });

  const handleTestConnection = async () => {
    setTestLoading(true);
    setTestResult(null);
    try {
      const result = await testLdapConnection();
      setTestResult(result);
    } catch (e: any) {
      setTestResult({ ok: false, error: e?.message || "Ошибка" });
    } finally {
      setTestLoading(false);
    }
  };

  if (isLoading) {
    return <div className="p-6 text-center"><Loader2 className="w-5 h-5 animate-spin mx-auto" /></div>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Server className="w-4 h-4" />
            Настройки LDAP
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Подключение к корпоративному каталогу для авторизации пользователей. После включения пользователи смогут входить под своими доменными учётными данными.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-3 rounded-md border bg-muted/30">
            <div>
              <p className="text-sm font-medium">Включить LDAP-авторизацию</p>
              <p className="text-xs text-muted-foreground">При включении локальная авторизация сохранится как резервная</p>
            </div>
            <Switch
              checked={currentForm.enabled}
              onCheckedChange={(v) => updateField("enabled", v)}
              data-testid="switch-ldap-enabled"
            />
          </div>

          {currentForm.enabled && (
            <>
              <div>
                <Label className="text-xs">Режим аутентификации</Label>
                <Select value={currentForm.authMode || "local"} onValueChange={(v) => updateField("authMode", v)}>
                  <SelectTrigger className="text-sm" data-testid="select-auth-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AUTH_MODES.map((m) => (
                      <SelectItem key={m.value} value={m.value}>
                        <span>{m.label}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-muted-foreground mt-1">
                  {AUTH_MODES.find((m) => m.value === (currentForm.authMode || "local"))?.desc}
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label className="text-xs">Адрес сервера (URL)</Label>
                  <Input
                    value={currentForm.serverUrl}
                    onChange={(e) => updateField("serverUrl", e.target.value)}
                    placeholder="ldap://ldap.example.com:389"
                    className="text-sm"
                    data-testid="input-ldap-url"
                  />
                </div>
                <div>
                  <Label className="text-xs">Base DN</Label>
                  <Input
                    value={currentForm.baseDN}
                    onChange={(e) => updateField("baseDN", e.target.value)}
                    placeholder="dc=example,dc=com"
                    className="text-sm"
                    data-testid="input-ldap-basedn"
                  />
                </div>
                <div>
                  <Label className="text-xs">Bind DN (учётная запись для поиска)</Label>
                  <Input
                    value={currentForm.bindDN}
                    onChange={(e) => updateField("bindDN", e.target.value)}
                    placeholder="cn=admin,dc=example,dc=com"
                    className="text-sm"
                    data-testid="input-ldap-binddn"
                  />
                </div>
                <div>
                  <Label className="text-xs">Bind Password</Label>
                  <Input
                    type="password"
                    value={currentForm.bindPassword}
                    onChange={(e) => updateField("bindPassword", e.target.value)}
                    placeholder="Пароль"
                    className="text-sm"
                    data-testid="input-ldap-bindpw"
                  />
                </div>
              </div>

              <div>
                <Label className="text-xs">Фильтр поиска</Label>
                <Input
                  value={currentForm.searchFilter}
                  onChange={(e) => updateField("searchFilter", e.target.value)}
                  placeholder="(uid={{username}})"
                  className="text-sm font-mono"
                  data-testid="input-ldap-filter"
                />
                <p className="text-[10px] text-muted-foreground mt-1">
                  {"{{username}}"} будет заменён на логин пользователя
                </p>
              </div>

              <div>
                <p className="text-xs font-medium mb-2">Маппинг атрибутов LDAP → пользователь</p>
                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <Label className="text-[10px] text-muted-foreground">Логин (атрибут)</Label>
                    <Input
                      value={currentForm.userAttributes?.username || ""}
                      onChange={(e) => updateAttr("username", e.target.value)}
                      placeholder="uid"
                      className="text-sm font-mono h-8"
                      data-testid="input-ldap-attr-username"
                    />
                  </div>
                  <div>
                    <Label className="text-[10px] text-muted-foreground">Имя (атрибут)</Label>
                    <Input
                      value={currentForm.userAttributes?.name || ""}
                      onChange={(e) => updateAttr("name", e.target.value)}
                      placeholder="cn"
                      className="text-sm font-mono h-8"
                      data-testid="input-ldap-attr-name"
                    />
                  </div>
                  <div>
                    <Label className="text-[10px] text-muted-foreground">Email (атрибут)</Label>
                    <Input
                      value={currentForm.userAttributes?.email || ""}
                      onChange={(e) => updateAttr("email", e.target.value)}
                      placeholder="mail"
                      className="text-sm font-mono h-8"
                      data-testid="input-ldap-attr-email"
                    />
                  </div>
                </div>
              </div>

              <div>
                <Label className="text-xs">Роль новых LDAP-пользователей</Label>
                <Select value={currentForm.defaultRole || "viewer"} onValueChange={(v) => updateField("defaultRole", v)}>
                  <SelectTrigger className="text-sm w-48" data-testid="select-default-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DEFAULT_ROLES.map((r) => (
                      <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-muted-foreground mt-1">
                  При первом входе через LDAP пользователь создаётся автоматически с этой ролью
                </p>
              </div>

              {/* Test connection */}
              <div className="flex items-center gap-2 p-3 rounded-md border bg-muted/20">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleTestConnection}
                  disabled={testLoading || !currentForm.serverUrl}
                  data-testid="button-test-ldap"
                >
                  {testLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Wifi className="w-3.5 h-3.5 mr-1" />}
                  Проверить подключение
                </Button>
                {testResult && (
                  <span className={`text-xs flex items-center gap-1 ${testResult.ok ? 'text-green-600' : 'text-destructive'}`}>
                    {testResult.ok ? (
                      <><Wifi className="w-3.5 h-3.5" /> Подключение успешно</>
                    ) : (
                      <><WifiOff className="w-3.5 h-3.5" /> {testResult.error}</>
                    )}
                  </span>
                )}
              </div>
            </>
          )}

          <div className="flex items-center gap-2 pt-2 border-t">
            <Button
              size="sm"
              onClick={() => saveMut.mutate(currentForm)}
              disabled={!dirty || saveMut.isPending}
              data-testid="button-save-ldap"
            >
              {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
              Сохранить
            </Button>
            {dirty && <span className="text-xs text-muted-foreground">Есть несохранённые изменения</span>}
          </div>

          {!currentForm.enabled && (
            <div className="flex items-center gap-2 p-3 rounded-md border border-dashed text-muted-foreground">
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <p className="text-xs">Включите LDAP, чтобы настроить параметры подключения и режим аутентификации</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
