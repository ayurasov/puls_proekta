import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../hooks/use-auth";
import { getAuthMode } from "../lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loader2, BarChart3, Server, KeyRound } from "lucide-react";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const { data: authMode } = useQuery({
    queryKey: ["/api/auth/mode"],
    queryFn: getAuthMode,
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    const result = await login(username, password);
    if (!result.ok) setError(result.error || "Ошибка входа");
    setLoading(false);
  };

  const mode = authMode?.mode || "local";
  const ldapEnabled = authMode?.ldapEnabled || false;

  const modeLabels: Record<string, { label: string; desc: string }> = {
    local: { label: "Локальная", desc: "Вход по логину и паролю системы" },
    ldap: { label: "Доменная (LDAP)", desc: "Вход по доменной учётной записи" },
    hybrid: { label: "Гибридная", desc: "Вход через LDAP или локально" },
  };

  const currentMode = modeLabels[mode] || modeLabels.local;

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm" data-testid="login-card">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center">
            <BarChart3 className="w-6 h-6 text-primary" />
          </div>
          <CardTitle className="text-xl">Пульс Проекта</CardTitle>
          <p className="text-sm text-muted-foreground">Войдите в систему</p>
          {ldapEnabled && (
            <div className="flex items-center justify-center gap-1.5 pt-1">
              <Badge variant="outline" className="text-[10px] gap-1">
                {mode === "ldap" ? <Server className="w-3 h-3" /> : mode === "hybrid" ? <Server className="w-3 h-3" /> : <KeyRound className="w-3 h-3" />}
                {currentMode.label}
              </Badge>
            </div>
          )}
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">Логин</Label>
              <Input
                id="username"
                data-testid="input-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={mode === "ldap" ? "domain\\user или user@domain" : "admin"}
                autoFocus
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Пароль</Label>
              <Input
                id="password"
                type="password"
                data-testid="input-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>
            {error && (
              <p className="text-sm text-destructive" data-testid="text-error">{error}</p>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={loading}
              data-testid="button-login"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
              Войти
            </Button>
          </form>
          {ldapEnabled && (
            <p className="mt-3 text-[10px] text-center text-muted-foreground">
              {currentMode.desc}
            </p>
          )}
          <p className="mt-4 text-xs text-center text-muted-foreground">
            © Пульс Проекта (©AlexYurasov)
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
