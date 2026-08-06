import { useState, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useAuth } from "../hooks/use-auth";
import { getProjects, getGroups, createProject, deleteProject, updateProject } from "../lib/api";
import { queryClient } from "../lib/queryClient";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { BarChart3, Plus, Trash2, Settings, LogOut, FolderOpen, Loader2, GripVertical, Pencil, Check, X } from "lucide-react";

export default function Dashboard() {
  const { user, logout } = useAuth();
  const [, navigate] = useLocation();
  const [newTitle, setNewTitle] = useState("");
  const [newGroupId, setNewGroupId] = useState<string>("");
  const [open, setOpen] = useState(false);

  const { data: projects = [], isLoading: loadingProjects } = useQuery({
    queryKey: ["/api/projects"],
    queryFn: getProjects,
  });

  const { data: groups = [] } = useQuery({
    queryKey: ["/api/groups"],
    queryFn: getGroups,
  });

  const createMut = useMutation({
    mutationFn: (d: any) => createProject(d),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/projects"] });
      setNewTitle("");
      setNewGroupId("");
      setOpen(false);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteProject(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/projects"] }),
  });

  const moveMut = useMutation({
    mutationFn: ({ id, groupId, sortOrder }: { id: number; groupId: number | null; sortOrder: number }) =>
      updateProject(id, { groupId, sortOrder }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["/api/projects"] }),
  });

  // Edit project state
  const [editingProject, setEditingProject] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editGroupId, setEditGroupId] = useState<string>("");

  const editMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: any }) => updateProject(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/projects"] });
      setEditingProject(null);
    },
  });

  const startEditProject = (p: any, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingProject(p.id);
    setEditTitle(p.title);
    setEditGroupId(p.groupId ? String(p.groupId) : "none");
  };

  const saveEditProject = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!editingProject || !editTitle.trim()) return;
    editMut.mutate({
      id: editingProject,
      data: {
        title: editTitle.trim(),
        groupId: editGroupId && editGroupId !== "none" ? parseInt(editGroupId) : null,
      },
    });
  };

  const cancelEditProject = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingProject(null);
  };

  const isAdmin = user?.role === "admin";
  const isManager = user?.role === "manager" || isAdmin;

  const roleLabels: Record<string, string> = {
    admin: "Администратор",
    manager: "Менеджер",
    editor: "Редактор",
    viewer: "Наблюдатель",
  };

  const accessLabels: Record<string, string> = {
    view: "Просмотр",
    edit: "Редактирование",
    manage: "Управление",
  };

  const groupMap = new Map(groups.map((g: any) => [g.id, g.name]));

  // Group projects by their groupId
  const grouped: { groupId: number | null; groupName: string; items: any[] }[] = [];
  const usedGroupIds = new Set<number>();

  // Collect group-specific projects
  for (const g of groups) {
    const items = projects
      .filter((p: any) => p.groupId === g.id)
      .sort((a: any, b: any) => (a.sortOrder || 0) - (b.sortOrder || 0));
    if (items.length > 0) {
      grouped.push({ groupId: g.id, groupName: g.name, items });
      usedGroupIds.add(g.id);
    }
  }

  // Projects without group
  const ungrouped = projects
    .filter((p: any) => !p.groupId || !usedGroupIds.has(p.groupId))
    .sort((a: any, b: any) => (a.sortOrder || 0) - (b.sortOrder || 0));
  if (ungrouped.length > 0 || grouped.length === 0) {
    grouped.push({ groupId: null, groupName: "Без направления", items: ungrouped });
  }

  // Drag state
  const dragRef = useRef<{ projectId: number; fromGroup: number | null } | null>(null);

  const onDragStart = (e: React.DragEvent, projectId: number, fromGroup: number | null) => {
    if (!isAdmin) return;
    dragRef.current = { projectId, fromGroup };
    e.dataTransfer.effectAllowed = "move";
    (e.target as HTMLElement).style.opacity = "0.5";
  };

  const onDragEnd = (e: React.DragEvent) => {
    (e.target as HTMLElement).style.opacity = "1";
    dragRef.current = null;
    // Remove all drop indicators
    document.querySelectorAll('[data-drop-indicator]').forEach(el => el.remove());
  };

  const onDragOver = (e: React.DragEvent) => {
    if (!dragRef.current || !isAdmin) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const onCardDragOver = (e: React.DragEvent, cardProjectId: number) => {
    if (!dragRef.current || !isAdmin || dragRef.current.projectId === cardProjectId) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
  };

  const onDrop = (e: React.DragEvent, targetGroupId: number | null, beforeProjectId?: number) => {
    e.preventDefault();
    e.stopPropagation();
    if (!dragRef.current || !isAdmin) return;
    const { projectId } = dragRef.current;
    if (beforeProjectId === projectId) { dragRef.current = null; return; }

    // Find target group items
    const grp = grouped.find(g => g.groupId === targetGroupId);
    const items = grp ? grp.items : [];

    let newOrder = 0;
    if (beforeProjectId) {
      const beforeItem = items.find((p: any) => p.id === beforeProjectId);
      newOrder = beforeItem ? (beforeItem.sortOrder || 0) - 1 : 0;
    } else {
      // Dropped on group area (after all items)
      const maxOrder = items.reduce((m: number, p: any) => Math.max(m, p.sortOrder || 0), 0);
      newOrder = maxOrder + 1;
    }

    moveMut.mutate({ id: projectId, groupId: targetGroupId, sortOrder: newOrder });
    dragRef.current = null;
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-card">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-3">
          <BarChart3 className="w-5 h-5 text-primary" />
          <span className="font-semibold text-sm">Пульс Проекта</span>
          <span className="flex-1" />
          <Badge variant="secondary" className="text-xs">
            {roleLabels[user?.role || ""] || user?.role}
          </Badge>
          <span className="text-sm text-muted-foreground">{user?.name}</span>
          {isAdmin && (
            <Button variant="ghost" size="sm" onClick={() => navigate("/admin")} data-testid="button-admin">
              <Settings className="w-4 h-4 mr-1" /> Админ
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={logout} data-testid="button-logout">
            <LogOut className="w-4 h-4" />
          </Button>
        </div>
      </header>

      <main className="max-w-6xl mx-auto p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Проекты</h1>
          {isManager && (
            <Dialog open={open} onOpenChange={setOpen}>
              <DialogTrigger asChild>
                <Button size="sm" data-testid="button-new-project">
                  <Plus className="w-4 h-4 mr-1" /> Новый проект
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Создать проект</DialogTitle>
                </DialogHeader>
                <div className="space-y-3">
                  <div>
                    <Label>Название</Label>
                    <Input
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                      placeholder="Мой проект"
                      data-testid="input-project-title"
                    />
                  </div>
                  {groups.length > 0 && (
                    <div>
                      <Label>Направление</Label>
                      <Select value={newGroupId} onValueChange={setNewGroupId}>
                        <SelectTrigger data-testid="select-project-group">
                          <SelectValue placeholder="Без направления" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">Без направления</SelectItem>
                          {groups.map((g: any) => (
                            <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                  <Button
                    className="w-full"
                    disabled={!newTitle.trim() || createMut.isPending}
                    onClick={() => createMut.mutate({
                      title: newTitle.trim(),
                      groupId: newGroupId && newGroupId !== "none" ? parseInt(newGroupId) : null,
                    })}
                    data-testid="button-create-project"
                  >
                    {createMut.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
                    Создать
                  </Button>
                </div>
              </DialogContent>
            </Dialog>
          )}
        </div>

        {loadingProjects ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : projects.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center text-muted-foreground">
              <FolderOpen className="w-10 h-10 mx-auto mb-3 opacity-40" />
              <p>Нет доступных проектов</p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6">
            {grouped.map(({ groupId, groupName, items }) => (
              <div
                key={groupId ?? "ungrouped"}
                onDragOver={onDragOver}
                onDrop={(e) => onDrop(e, groupId)}
              >
                <div className="flex items-center gap-2 mb-2">
                  <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">{groupName}</h2>
                  <Badge variant="outline" className="text-[10px]">{items.length}</Badge>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {items.map((p: any) => (
                    <Card
                      key={p.id}
                      className={`cursor-pointer hover:border-primary/40 transition-colors ${editingProject === p.id ? 'border-primary ring-1 ring-primary/20' : ''}`}
                      onClick={() => editingProject === p.id ? undefined : navigate(`/project/${p.id}`)}
                      data-testid={`card-project-${p.id}`}
                      draggable={isAdmin && editingProject !== p.id}
                      onDragStart={(e) => onDragStart(e, p.id, groupId)}
                      onDragEnd={onDragEnd}
                      onDragOver={(e) => onCardDragOver(e, p.id)}
                      onDrop={(e) => onDrop(e, groupId, p.id)}
                    >
                      <CardHeader className="pb-2">
                        {editingProject === p.id ? (
                          <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                            <div>
                              <Input
                                value={editTitle}
                                onChange={(e) => setEditTitle(e.target.value)}
                                className="h-8 text-sm"
                                placeholder="Название проекта"
                                autoFocus
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" && editTitle.trim()) saveEditProject(e as any);
                                  if (e.key === "Escape") setEditingProject(null);
                                }}
                                data-testid={`input-edit-title-${p.id}`}
                              />
                            </div>
                            {groups.length > 0 && (
                              <div>
                                <Select value={editGroupId} onValueChange={setEditGroupId}>
                                  <SelectTrigger className="h-8 text-xs" data-testid={`select-edit-group-${p.id}`}>
                                    <SelectValue placeholder="Без направления" />
                                  </SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value="none">Без направления</SelectItem>
                                    {groups.map((g: any) => (
                                      <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </div>
                            )}
                            <div className="flex gap-1 justify-end">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 w-7 p-0 text-green-600"
                                onClick={saveEditProject}
                                disabled={!editTitle.trim() || editMut.isPending}
                                data-testid={`button-save-edit-${p.id}`}
                              >
                                {editMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 w-7 p-0"
                                onClick={cancelEditProject}
                                data-testid={`button-cancel-edit-${p.id}`}
                              >
                                <X className="w-3.5 h-3.5" />
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <CardTitle className="text-sm font-medium flex items-center justify-between">
                            <span className="flex items-center gap-1.5 truncate">
                              {isAdmin && <GripVertical className="w-3.5 h-3.5 text-muted-foreground/40 flex-shrink-0 cursor-grab" />}
                              <span className="truncate">{p.title}</span>
                            </span>
                            {isAdmin && (
                              <div className="flex gap-0.5 flex-shrink-0">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 w-7 p-0 text-muted-foreground hover:text-primary"
                                  onClick={(e) => startEditProject(p, e)}
                                  data-testid={`button-edit-project-${p.id}`}
                                >
                                  <Pencil className="w-3.5 h-3.5" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                                  onClick={(e) => { e.stopPropagation(); deleteMut.mutate(p.id); }}
                                  data-testid={`button-delete-project-${p.id}`}
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </Button>
                              </div>
                            )}
                          </CardTitle>
                        )}
                      </CardHeader>
                      {editingProject !== p.id && (
                        <CardContent className="pt-0">
                          <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
                            {p._accessLevel && (
                              <Badge variant={p._accessLevel === "view" ? "secondary" : "default"} className="text-[10px]">
                                {accessLabels[p._accessLevel] || p._accessLevel}
                              </Badge>
                            )}
                            {p.updatedAt && <span>Обновлён: {new Date(p.updatedAt).toLocaleDateString("ru")}</span>}
                          </div>
                        </CardContent>
                      )}
                    </Card>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
