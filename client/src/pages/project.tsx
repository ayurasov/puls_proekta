import { useEffect, useRef, useState, useCallback } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useAuth } from "../hooks/use-auth";
import { getProject, updateProject } from "../lib/api";
import { queryClient } from "../lib/queryClient";
import { useLocation, useParams } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ArrowLeft, Save, Loader2, Eye, Edit, Lock, Check } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = parseInt(id || "0");
  const { user } = useAuth();
  const [, navigate] = useLocation();
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const { toast } = useToast();
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const latestData = useRef<string | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const iframeReady = useRef(false);
  const dataSentForProject = useRef<number | null>(null);

  const { data: project, isLoading } = useQuery({
    queryKey: ["/api/projects", projectId],
    queryFn: () => getProject(projectId),
    enabled: projectId > 0,
    staleTime: 0,
  });

  const saveMutRef = useRef<((data: string) => void) | null>(null);

  const saveMut = useMutation({
    mutationFn: (data: string) => updateProject(projectId, { data }),
    onSuccess: (_result, savedData) => {
      queryClient.setQueryData(["/api/projects", projectId], (old: any) => {
        if (old) return { ...old, data: savedData };
        return old;
      });
      setDirty(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
    onError: () => {
      toast({ title: "Ошибка сохранения", variant: "destructive" });
    },
  });

  // Keep a stable ref to mutate
  saveMutRef.current = (data: string) => saveMut.mutate(data);

  const accessLevel = project?._accessLevel || "view";
  const canEdit = accessLevel === "edit" || accessLevel === "manage" || user?.role === "admin";
  const canEditRef = useRef(canEdit);
  canEditRef.current = canEdit;

  // Stable ref for project data to avoid closure staleness
  const projectRef = useRef(project);
  projectRef.current = project;

  // Send load-project to iframe — called when both iframe is ready AND data is available
  const sendLoadProject = useCallback(() => {
    const p = projectRef.current;
    if (!p || !iframeRef.current?.contentWindow) return;
    if (dataSentForProject.current === p.id) return; // already sent for this project
    const data = p.data || "{}";
    iframeRef.current.contentWindow.postMessage(
      { type: "load-project", data, readOnly: !canEditRef.current, title: p.title, userName: user?.name || 'Автор' },
      "*"
    );
    dataSentForProject.current = p.id;
  }, []);

  // Listen for messages from the gantt iframe — stable effect, no deps
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data?.type === "gantt-ready") {
        // iframe reports it's ready to receive messages
        iframeReady.current = true;
        sendLoadProject();
      }
      if (e.data?.type === "gantt-state-changed") {
        latestData.current = e.data.state;
        setDirty(true);
        setSaved(false);
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        saveTimerRef.current = setTimeout(() => {
          if (latestData.current && canEditRef.current && saveMutRef.current) {
            saveMutRef.current(latestData.current);
          }
        }, 2000);
      }
      if (e.data?.type === "gantt-state-response") {
        if (latestData.current === null) {
          latestData.current = e.data.state;
        }
        saveMutRef.current?.(e.data.state);
      }
    };
    window.addEventListener("message", handler);
    return () => {
      window.removeEventListener("message", handler);
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [sendLoadProject]);

  // Reset state on project change
  useEffect(() => {
    latestData.current = null;
    iframeReady.current = false;
    dataSentForProject.current = null;
    setDirty(false);
    setSaved(false);
  }, [projectId]);

  // When project data arrives/changes, try to send to iframe (if it's ready)
  useEffect(() => {
    if (project && iframeReady.current) {
      // Data arrived after iframe was already ready — reset sent flag to allow re-send
      dataSentForProject.current = null;
      sendLoadProject();
    }
  }, [project, canEdit, sendLoadProject]);

  // Also handle iframe onLoad as a fallback (in case gantt-ready was missed)
  const onIframeLoad = useCallback(() => {
    // Give the iframe a moment to register its message listener
    setTimeout(() => {
      if (!iframeReady.current) {
        // gantt-ready wasn't received — force send anyway
        iframeReady.current = true;
        sendLoadProject();
      }
    }, 200);
  }, [sendLoadProject]);

  const handleSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    if (latestData.current) {
      saveMut.mutate(latestData.current);
    } else {
      iframeRef.current?.contentWindow?.postMessage({ type: "request-state" }, "*");
    }
  };

  const ganttUrl = `/gantt.html`;

  const levelLabels: Record<string, string> = {
    view: "Просмотр",
    edit: "Редактирование",
    manage: "Полное управление",
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="min-h-screen flex items-center justify-center flex-col gap-3">
        <Lock className="w-8 h-8 text-muted-foreground" />
        <p className="text-muted-foreground">Проект не найден или нет доступа</p>
        <Button variant="outline" onClick={() => navigate("/")}>На главную</Button>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      <header className="border-b bg-card flex-shrink-0">
        <div className="px-4 h-12 flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate("/")} data-testid="button-back">
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <span className="font-semibold text-sm truncate">{project.title}</span>
          <Badge variant={canEdit ? "default" : "secondary"} className="text-[10px] flex items-center gap-1">
            {canEdit ? <Edit className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
            {levelLabels[accessLevel] || accessLevel}
          </Badge>
          <span className="flex-1" />
          {saved && (
            <span className="text-xs text-green-600 flex items-center gap-1">
              <Check className="w-3 h-3" /> Сохранено
            </span>
          )}
          {canEdit && (
            <Button
              size="sm"
              onClick={handleSave}
              disabled={saveMut.isPending || !dirty}
              data-testid="button-save"
            >
              {saveMut.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Save className="w-4 h-4 mr-1" />}
              Сохранить
            </Button>
          )}
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <iframe
          key={projectId}
          ref={iframeRef}
          src={ganttUrl}
          onLoad={onIframeLoad}
          className="w-full h-full border-0"
          data-testid="iframe-gantt"
        />
      </div>
    </div>
  );
}
